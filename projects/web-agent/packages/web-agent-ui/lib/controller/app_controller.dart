import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/model_catalog.dart';
import '../models/schema.dart';
import '../services/daemon_client.dart';
import '../services/isar_service.dart';
import '../services/web_agent_gateway.dart';

class ChatTurn {
  ChatTurn({required this.userMessage, required this.replies});

  final ChatMessage userMessage;
  final List<ChatMessage> replies;

  bool get done => replies.every((message) => message.status != 'pending');
}

class SendException implements Exception {
  SendException(this.message);

  final String message;

  @override
  String toString() => message;
}

class AppController extends ChangeNotifier {
  AppController({
    required this.isarService,
    required this.daemonClient,
  });

  final IsarService isarService;
  final DaemonClient daemonClient;

  WebAgentGateway? _gateway;

  List<ChatWindow> windows = [];
  int? activeWindowId;

  DaemonStatus? daemonStatus;

  /// windowId -> 消息列表（读多写少，直接查询）
  final Map<int, List<ChatMessage>> _messagesByWindow = {};
  final Map<int, List<ChatTurn>> _turnsByWindow = {};
  final Map<int, List<String>> _selectedModelsByWindow = {};

  bool sending = false;
  bool stopping = false;
  String? lastError;

  Duration pollInterval = const Duration(seconds: 2);
  final Map<int, Timer> _pollers = {};
  final Map<int, bool> _runningByWindow = {};

  /// windowId -> 正在等待回复的任务
  final Map<int, ({String chatId, String messageId, ChatMessage userSnapshot})>
      _activeTaskByWindow = {};

  Future<void> init() async {
    await isarService.open();
    windows = await isarService.loadWindows();
    for (final window in windows) {
      _messagesByWindow[window.id] = await isarService.loadMessages(window.id);
      _turnsByWindow[window.id] = [];
      _selectedModelsByWindow[window.id] = List<String>.from(window.modelIds);
      _rebuildTurns(window.id);
    }
    if (windows.isEmpty) {
      await createWindow();
    }
    activeWindowId ??= windows.isEmpty ? null : windows.first.id;
    await refreshDaemonStatus();
    notifyListeners();
  }

  List<ChatWindow> get windowList => windows;

  ChatWindow? get activeWindow {
    if (activeWindowId == null) return null;
    for (final window in windows) {
      if (window.id == activeWindowId) return window;
    }
    return null;
  }

  List<ChatMessage> messagesFor(int windowId) => _messagesByWindow[windowId] ?? const [];

  List<ChatTurn> turnsFor(int windowId) => _turnsByWindow[windowId] ?? const [];

  List<String> selectedModelsFor(int windowId) =>
      _selectedModelsByWindow[windowId] ?? const [];

  bool runningFor(int windowId) =>
      _runningByWindow[windowId] ?? false;

  Future<ChatWindow> createWindow() async {
    final now = DateTime.now();
    final window = ChatWindow()
      ..title = '新窗口 ${windows.length + 1}'
      ..createdAt = now
      ..updatedAt = now;
    await isarService.upsertWindow(window);
    windows = List<ChatWindow>.from(windows)..add(window);
    _messagesByWindow[window.id] = [];
    _turnsByWindow[window.id] = [];
    _selectedModelsByWindow[window.id] = ['deepseek'];
    activeWindowId = window.id;
    notifyListeners();
    return window;
  }

  Future<void> stopTask(int windowId) async {
    final active = _activeTaskByWindow[windowId];
    _pollers.remove(windowId)?.cancel();
    _runningByWindow[windowId] = false;
    notifyListeners();
    if (active == null || stopping) return;
    stopping = true;
    notifyListeners();
    try {
      final gateway = _gateway ??= WebAgentGateway(daemonClient);
      await gateway.stopTask(
        chatId: active.chatId,
        messageId: active.messageId,
      );
      final result = await gateway.readTask(
        chatId: active.chatId,
        messageId: active.messageId,
      );
      _applyTaskResult(windowId, active.userSnapshot, result);
    } catch (error) {
      lastError = error.toString();
      rethrow;
    } finally {
      _activeTaskByWindow.remove(windowId);
      stopping = false;
      notifyListeners();
    }
  }

  Future<void> closeWindow(int windowId) async {
    _pollers.remove(windowId)?.cancel();
    _activeTaskByWindow.remove(windowId);
    _runningByWindow.remove(windowId);
    _messagesByWindow.remove(windowId);
    _turnsByWindow.remove(windowId);
    _selectedModelsByWindow.remove(windowId);
    final window = windows.where((item) => item.id == windowId).firstOrNull;
    windows = windows.where((item) => item.id != windowId).toList();
    if (window != null) await isarService.deleteWindow(window);
    if (activeWindowId == windowId) {
      activeWindowId = windows.isEmpty ? null : windows.last.id;
    }
    notifyListeners();
  }

  void selectWindow(int windowId) {
    if (!windows.any((item) => item.id == windowId)) return;
    activeWindowId = windowId;
    notifyListeners();
  }

  void toggleModel(int windowId, String modelId) {
    final selected = List<String>.from(_selectedModelsByWindow[windowId] ?? const []);
    if (selected.contains(modelId)) {
      selected.remove(modelId);
    } else {
      selected.add(modelId);
    }
    _selectedModelsByWindow[windowId] = selected;
    final window = windows.where((item) => item.id == windowId).firstOrNull;
    if (window != null) {
      window.modelIds = selected;
      window.updatedAt = DateTime.now();
      unawaited(isarService.upsertWindow(window));
    }
    notifyListeners();
  }

  Future<void> refreshDaemonStatus() async {
    daemonStatus = await daemonClient.status();
    notifyListeners();
  }

  Future<List<TaskReply>> sendMessage(
    int windowId,
    String content,
    List<String> mentionModels,
  ) async {
    if (content.trim().isEmpty) return const [];
    if (sending) throw SendException('已有任务正在发送，请稍候');

    final window = windows.where((item) => item.id == windowId).firstOrNull;
    if (window == null) throw SendException('窗口不存在');

    sending = true;
    lastError = null;
    notifyListeners();

    try {
      final mentionOnly = mentionModels.isNotEmpty;
      final modelIds = mentionOnly
          ? mentionModels
          : List<String>.from(_selectedModelsByWindow[windowId] ?? const []);
      if (modelIds.isEmpty) {
        throw SendException('请先在下方选择至少一个 Web-Agent 模型，或使用 @模型 指定');
      }

      final gateway = _gateway ??= WebAgentGateway(daemonClient);
      final now = DateTime.now();
      final userSnapshot = ChatMessage()
        ..windowId = windowId
        ..role = 'user'
        ..modelId = ''
        ..modelName = ''
        ..content = content
        ..status = 'pending'
        ..createdAt = now
        ..updatedAt = now;
      _appendMessage(windowId, userSnapshot, persists: true);

      var chatId = window.chatId;
      final chatRef = await gateway.ensureChat(
        existingChatId: chatId,
        chatName: window.title,
        modelIds: modelIds,
      );
      chatId = chatRef.chatId;
      if (window.chatId != chatId) {
        window.chatId = chatId;
        window.updatedAt = DateTime.now();
        unawaited(isarService.upsertWindow(window));
      }

      final task = await gateway.postTask(
        chatId: chatId,
        roleIds: chatRef.roleIds,
        content: content,
      );

      userSnapshot.messageId = task.messageId;
      userSnapshot.groupId = task.messageId;
      await isarService.upsertMessage(userSnapshot);

      final replies = <ChatMessage>[];
      for (final reply in task.replies) {
        replies.add(_replyToMessage(windowId, userSnapshot, reply));
      }
      if (replies.isNotEmpty) {
        await isarService.upsertMessages(replies);
      }

      _startPolling(
        windowId,
        chatId: chatId,
        messageId: task.messageId,
        userSnapshot: userSnapshot,
      );
      _runningByWindow[windowId] = true;

      sending = false;
      notifyListeners();
      return task.replies;
    } on Exception catch (error) {
      sending = false;
      lastError = error is SendException ? error.message : error.toString();
      notifyListeners();
      rethrow;
    }
  }

  void _startPolling(
    int windowId, {
    required String chatId,
    required String messageId,
    required ChatMessage userSnapshot,
  }) {
    _activeTaskByWindow[windowId] = (
      chatId: chatId,
      messageId: messageId,
      userSnapshot: userSnapshot,
    );
    _pollers.remove(windowId)?.cancel();
    final timer = Timer.periodic(pollInterval, (_) async {
      final gateway = _gateway ??= WebAgentGateway(daemonClient);
      try {
        final result = await gateway.readTask(chatId: chatId, messageId: messageId);
        _applyTaskResult(windowId, userSnapshot, result);
        if (result.pendingRoleIds.isEmpty) {
          _pollers.remove(windowId)?.cancel();
          final active = _activeTaskByWindow[windowId];
          if (active != null && active.messageId == messageId) {
            _runningByWindow[windowId] = false;
          }
        }
      } catch (_) {
        // 轮询失败则下次再试
      }
    });
    _pollers[windowId] = timer;
  }

  void _applyTaskResult(int windowId, ChatMessage userSnapshot, TaskResult result) {
    final now = DateTime.now();
    final replies = _messagesByWindow[windowId]
        ?.where((message) =>
            message.role == 'assistant' && message.groupId == userSnapshot.messageId)
        .toList() ??
        <ChatMessage>[];
    final byMessageId = <String, ChatMessage>{
      for (final reply in replies) reply.messageId ?? '': reply,
    };
    for (final reply in result.replies) {
      final existing = byMessageId[reply.messageId];
      if (existing != null) {
        if (existing.content != reply.content ||
            existing.status != reply.status ||
            existing.conversationUrl != reply.conversationUrl) {
          existing.content = reply.content;
          existing.status = reply.status;
          existing.conversationUrl = reply.conversationUrl;
          existing.updatedAt = now;
          unawaited(isarService.upsertMessage(existing));
        }
      } else {
        final created = _replyToMessage(windowId, userSnapshot, reply);
        final placeholder = _messagesByWindow[windowId]
            ?.where((message) =>
                message.role == 'assistant' &&
                (message.messageId ?? '').isEmpty &&
                message.modelId == reply.roleId &&
                message.groupId == userSnapshot.messageId)
            .firstOrNull;
        if (placeholder != null) {
          _messagesByWindow[windowId]?.remove(placeholder);
          unawaited(isarService.deleteMessage(placeholder.id));
        }
        _messagesByWindow[windowId]?.add(created);
        unawaited(isarService.upsertMessage(created));
      }
    }
    for (final pending in result.pendingRoleIds) {
      final existing = _messagesByWindow[windowId]
          ?.where((message) =>
              message.role == 'assistant' &&
              message.modelId == pending &&
              message.groupId == userSnapshot.messageId)
          .firstOrNull;
      if (existing != null) {
        existing.status = 'pending';
      } else {
        _appendMessage(
          windowId,
          ChatMessage()
            ..windowId = windowId
            ..role = 'assistant'
            ..modelId = pending
            ..modelName = pending
            ..content = '等待回复…'
            ..status = 'pending'
            ..groupId = userSnapshot.messageId
            ..createdAt = DateTime.now()
            ..updatedAt = DateTime.now(),
          persists: true,
        );
      }
    }
    _rebuildTurns(windowId);
    notifyListeners();
  }

  ChatMessage _replyToMessage(int windowId, ChatMessage user, TaskReply reply) {
    final now = DateTime.now();
    return ChatMessage()
      ..windowId = windowId
      ..role = 'assistant'
      ..modelId = reply.roleId
      ..modelName = reply.roleName
      ..content = reply.content
      ..status = reply.status
      ..messageId = reply.messageId
      ..groupId = user.messageId
      ..conversationUrl = reply.conversationUrl
      ..createdAt = now
      ..updatedAt = now;
  }

  void _appendMessage(int windowId, ChatMessage message, {bool persists = false}) {
    final list = _messagesByWindow.putIfAbsent(windowId, () => []);
    list.add(message);
    if (persists) unawaited(isarService.upsertMessage(message));
    _rebuildTurns(windowId);
  }

  void _rebuildTurns(int windowId) {
    final messages = _messagesByWindow[windowId] ?? const <ChatMessage>[];
    final turns = <ChatTurn>[];
    ChatMessage? currentUser;
    final currentReplies = <ChatMessage>[];
    for (final message in messages) {
      if (message.role == 'user') {
        if (currentUser != null) {
          turns.add(ChatTurn(userMessage: currentUser, replies: List.of(currentReplies)));
        }
        currentUser = message;
        currentReplies.clear();
      } else if (message.role == 'assistant') {
        if (currentUser != null && message.groupId == currentUser.messageId) {
          currentReplies.add(message);
        }
      }
    }
    if (currentUser != null) {
      turns.add(ChatTurn(userMessage: currentUser, replies: List.of(currentReplies)));
    }
    _turnsByWindow[windowId] = turns;
  }

  List<AiModel> allModels() => webAgentModels;

  @override
  void dispose() {
    for (final timer in _pollers.values) {
      timer.cancel();
    }
    super.dispose();
  }
}

extension FirstOrNull<T> on Iterable<T> {
  T? get firstOrNull {
    for (final item in this) {
      return item;
    }
    return null;
  }
}