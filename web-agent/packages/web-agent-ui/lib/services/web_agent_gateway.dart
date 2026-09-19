import 'dart:async';

import '../models/model_catalog.dart';
import 'daemon_client.dart';

class ChatRef {
  const ChatRef({required this.chatId, required this.roleIds});

  final String chatId;
  final List<String> roleIds;
}

class TaskReply {
  const TaskReply({
    required this.messageId,
    required this.roleId,
    required this.roleName,
    required this.content,
    required this.status,
    this.conversationUrl,
  });

  final String messageId;
  final String roleId;
  final String roleName;
  final String content;
  final String status;
  final String? conversationUrl;
}

class TaskResult {
  const TaskResult({
    required this.chatId,
    required this.messageId,
    required this.replies,
    required this.pendingRoleIds,
    required this.errorRoleIds,
  });

  final String chatId;
  final String messageId;
  final List<TaskReply> replies;
  final List<String> pendingRoleIds;
  final List<String> errorRoleIds;

  bool get finished => pendingRoleIds.isEmpty;
}

class GatewayCommandException implements Exception {
  const GatewayCommandException(this.message);

  final String message;

  @override
  String toString() => 'GatewayCommandException: $message';
}

class WebAgentGateway {
  WebAgentGateway(this._client);

  final DaemonClient _client;

  Future<ChatRef> ensureChat({
    required String? existingChatId,
    required String chatName,
    required List<String> modelIds,
  }) async {
    var chatId = existingChatId;
    if (chatId == null) {
      final created = await _client.command(
        'chat.create',
        {'name': chatName, 'mode': 'independent'},
      );
      if (created.failed) throw gatewayException(created);
      chatId = _extractChatId(created.data);
      if (chatId == null) {
        throw StateError('聊天创建失败：未返回聊天 ID');
      }
    }

    final current = await _client.command('chat.get', {'chatId': chatId});
    final roles = _rolesOf(current.data);
    final existingSites = roles
        .map((role) => role['chatSite'] as String?)
        .whereType<String>()
        .map((site) => site.toLowerCase())
        .toSet();

    final missing = modelIds.where((id) => !existingSites.contains(id)).toList();
    final roleIds = <String>[
      ...roles.map((role) => role['id'] as String? ?? '').where((id) => id.isNotEmpty),
    ];

    if (missing.isNotEmpty) {
      final added = await _client.command(
        'roles.batchAdd',
        {
          'chatId': chatId,
          'items': missing.map((id) {
            return {
              'source': 'temporary',
              'name': modelDisplayName(id),
              'chatSite': id,
              'systemPrompt': parallelSystemPrompt,
            };
          }).toList(),
        },
      );
      if (added.failed) throw gatewayException(added);
      final newRoles = _rolesOf(added.data);
      roleIds.addAll(
        newRoles
            .map((role) => role['id'] as String? ?? '')
            .where((id) => id.isNotEmpty),
      );
    }

    // 主动激活并初始化角色绑定（幂等，非致命）
    await _client.command('chat.initialize', {
      'chatId': chatId,
      'waitForReady': false,
      'timeoutMs': 10,
    });

    return ChatRef(chatId: chatId, roleIds: List.unmodifiable(roleIds));
  }

  Future<TaskResult> postTask({
    required String chatId,
    required List<String> roleIds,
    required String content,
  }) async {
    final posted = await _client.command(
      'task.post',
      {
        'chatId': chatId,
        'target': roleIds.isEmpty ? 'all' : {'roleIds': roleIds},
        'content': content,
      },
    );
    if (posted.failed) throw gatewayException(posted);
    final rawData = posted.data;
    final message = rawData is Map ? rawData['message'] : null;
    final messageId = message is Map ? message['id'] as String? : null;
    if (messageId == null) {
      throw StateError('任务已发布但未返回消息 ID');
    }
    return readTask(chatId: chatId, messageId: messageId);
  }

  Future<TaskResult> readTask({
    required String chatId,
    required String messageId,
  }) async {
    final read = await _client.command(
      'task.read',
      {'chatId': chatId, 'messageId': messageId},
    );
    if (read.failed) throw gatewayException(read);
    return parseTaskResult(read.data);
  }

  Future<void> stopTask({
    required String chatId,
    String? messageId,
    List<String>? roleIds,
  }) async {
    final stopped = await _client.command(
      'task.stop',
      {
        'chatId': chatId,
        'messageId': ?messageId,
        if (roleIds != null && roleIds.isNotEmpty) 'roleIds': roleIds,
      },
    );
    if (stopped.failed) throw gatewayException(stopped);
  }

  Future<TaskResult> waitForTask({
    required String chatId,
    required String messageId,
    Duration timeout = const Duration(minutes: 3),
  }) async {
    final requestTimeoutMs = timeout.inMilliseconds + 30000;
    final waited = await _client.command(
      'task.wait',
      {'chatId': chatId, 'messageId': messageId, 'timeoutMs': timeout.inMilliseconds},
      timeoutMs: requestTimeoutMs,
    );
    if (waited.failed) throw gatewayException(waited);
    return parseTaskResult(waited.data);
  }

  TaskResult parseTaskResult(Object? data) {
    final map = data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
    final replies = <TaskReply>[];
    final rawReplies = map['replies'];
    if (rawReplies is List) {
      for (final item in rawReplies) {
        if (item is! Map) continue;
        replies.add(TaskReply(
          messageId: item['messageId'] as String? ?? '',
          roleId: item['roleId'] as String? ?? '',
          roleName: item['roleName'] as String? ?? '未知人员',
          content: item['content'] as String? ?? '',
          status: item['status'] as String? ?? 'pending',
          conversationUrl: item['conversationUrl'] as String?,
        ));
      }
    }
    return TaskResult(
      chatId: map['chatId'] as String? ?? '',
      messageId: map['messageId'] as String? ?? '',
      replies: List.unmodifiable(replies),
      pendingRoleIds: _stringList(map['pendingRoleIds']),
      errorRoleIds: _stringList(map['errorRoleIds']),
    );
  }

  List<String> _stringList(Object? value) {
    if (value is! List) return const [];
    return value.map((item) => item.toString()).toList();
  }

  List<Map<String, dynamic>> _rolesOf(Object? data) {
    if (data is! Map) return const [];
    final roles = data['roles'];
    if (roles is! List) return const [];
    return roles
        .whereType<Map>()
        .map((item) => Map<String, dynamic>.from(item))
        .toList();
  }

  String? _extractChatId(Object? data) {
    if (data is Map) {
      final chat = data['chat'];
      if (chat is Map) return chat['id'] as String?;
    }
    return null;
  }

  Exception gatewayException(CommandResult result) {
    return GatewayCommandException(
      '${result.message ?? '命令执行失败'}'
      '${result.hint != null ? '（${result.hint}）' : ''}',
    );
  }
}

String modelDisplayName(String modelId) {
  for (final model in webAgentModels) {
    if (model.id == modelId) return model.name;
  }
  return modelId;
}