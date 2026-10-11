import 'dart:async';

import 'package:flutter/foundation.dart';

import '../services/opencode_client.dart';

/// 待发送附件（点 + 选择文件后进入输入框上方白色附件栏）。
class OpencodeAttachment {
  const OpencodeAttachment({
    required this.path,
    required this.name,
    required this.mime,
  });

  final String path;
  final String name;
  final String mime;
}

/// 发送时目标会话正被别处（终端等）占用，且未显式选择接管（takeover）。
class ExternalBusyException implements Exception {
  ExternalBusyException(this.sessionId);

  final String sessionId;

  @override
  String toString() => 'ExternalBusyException: 会话 $sessionId 正在 opencode 终端处理中';
}

class OpencodeController extends ChangeNotifier {
  OpencodeController({OpencodeClient? client})
    : _client = client ?? OpencodeClient();

  final OpencodeClient _client;
  Timer? _healthTimer;
  Timer? _pollTimer;

  bool serverReachable = false;
  String? serverVersion;
  bool loading = false;
  String? lastError;

  List<OpencodeSessionData> sessions = [];
  String? activeSessionId;
  List<OpencodeMessage> messages = [];

  List<OpencodeModel> models = [];
  String? selectedModelId; // 形如 providerId/modelId

  List<OpencodeCommand> commands = [];

  /// 本轮发送的 agent 模式：build / plan（Tab 切换）。
  String agentMode = 'build';

  bool get planMode => agentMode == 'plan';

  /// 本端自己发起的待回复会话，用于区分"我发起的生成"与"别处触发的 busy"。
  final Set<String> _ownPending = {};

  /// 各会话 busy 集合（由 SSE + /session/status 维护）。
  final Set<String> _busySessions = {};

  /// 服务端曾报告 busy 的会话。只在真实 busy→idle 转变时清 ownPending，
  /// 防止"轮询快照发出时本端发送尚未被服务端处理"的陈旧 idle 误清。
  final Set<String> _serverBusySeen = {};

  /// 本端最近一次为该会话发起发送的时间：窗口内的陈旧 idle 快照不清
  /// ownPending（覆盖接管场景 abort→prompt_async 之间的瞬间 idle）。
  final Map<String, DateTime> _lastOwnSendAt = {};

  /// 当前会话是否处于生成状态（本端或别处触发，决定模型下拉禁用等）。
  bool get sending {
    final id = activeSessionId;
    return id != null && _busySessions.contains(id);
  }

  /// 当前会话是否为本端发起生成（决定输入区禁用、停止按钮显示）。
  bool get sendingOwn {
    final id = activeSessionId;
    return id != null && _ownPending.contains(id);
  }

  /// 当前会话外部忙且非本端发起（用于琥珀色提示横幅 + 接管确认）。
  bool get externalBusyForActive {
    final id = activeSessionId;
    return id != null && _busySessions.contains(id) && !_ownPending.contains(id);
  }

  bool get anyBusy => _busySessions.isNotEmpty;

  /// 指定会话当前是否被外部（终端等）占用且非本端发起（用于删除等场景提醒）。
  bool isSessionExternallyActive(String sessionId) =>
      _busySessions.contains(sessionId) && !_ownPending.contains(sessionId);

  /// 待应答的权限请求（permission.asked）。
  OpencodePermissionRequest? pendingPermission;

  /// 待应答的选择题组（question.asked，数组即多步骤）。
  OpencodeQuestionRequest? pendingQuestion;

  /// 输入框附件栏：发送时随文本一并作为 file parts。
  List<OpencodeAttachment> attachments = [];

  StreamSubscription<OpencodeEvent>? _eventSub;
  StreamSubscription<OpencodePermissionRequest>? _permissionSub;
  StreamSubscription<OpencodeQuestionRequest>? _questionSub;
  int _pollTick = 0;
  DateTime _lastMessageLoad = DateTime.fromMillisecondsSinceEpoch(0);

  /// 顶层会话列表（剔除子会话），避免列表被 subagent 子任务刷屏。
  List<OpencodeSessionData> get rootSessions =>
      sessions.where((session) => session.parentId == null).toList();

  OpencodeSessionData? get activeSession {
    for (final session in sessions) {
      if (session.id == activeSessionId) return session;
    }
    return null;
  }

  String? get activeModelLabel {
    for (final model in models) {
      if (model.id == selectedModelId) return model.label;
    }
    return null;
  }

  Future<void> init() async {
    await refresh(); // 健康检查 + 模型 + 会话
    _listenEvents();
    _healthTimer = Timer.periodic(const Duration(seconds: 8), (_) {
      healthCheck();
    });
    _ensurePolling(); // 兜底轮询常驻：SSE 失效时靠 /session/status 发现外部忙态
  }

  void _listenEvents() {
    _permissionSub = _client.permissionRequests.listen((request) {
      pendingPermission = request;
      notifyListeners();
    });
    _questionSub = _client.questionRequests.listen((request) {
      pendingQuestion = request;
      notifyListeners();
    });
    _eventSub = _client.events.listen(_onEvent);
  }

  void _onEvent(OpencodeEvent event) {
    final props = event.properties ?? const <String, dynamic>{};
    switch (event.type) {
      case 'session.idle':
        final id = props['sessionID'] as String? ?? props['id'] as String?;
        if (id != null) {
          _ownPending.remove(id);
          _setBusy(id, false);
        }
        break;
      case 'session.status':
        _updateBusyFromEvent(props);
        break;
      case 'message.updated':
        final id = props['sessionID'] as String?;
        if (id != null && id == activeSessionId) {
          final now = DateTime.now();
          if (now.difference(_lastMessageLoad).inMilliseconds >= 250) {
            _lastMessageLoad = now;
            unawaited(_loadMessages(id));
          }
        }
        break;
      case 'session.created':
      case 'session.deleted':
      case 'session.updated':
        unawaited(_refreshSessionsOnly());
        break;
    }
  }

  void _updateBusyFromEvent(Map<String, dynamic> props) {
    final id = props['sessionID'] as String?;
    if (id == null) return;
    final status = props['status'];
    var busy = false;
    if (status is Map) {
      busy = status['type'] == 'busy' || status['type'] == 'retry';
    } else if (status is String) {
      busy = status == 'busy' || status == 'retry';
    } else if (props['busy'] is bool) {
      busy = props['busy'] == true;
    }
    _setBusy(id, busy);
  }

  void _setBusy(String sessionId, bool busy) {
    if (busy) {
      _busySessions.add(sessionId);
      _serverBusySeen.add(sessionId);
      // 任何会话变忙即确保兜底轮询在跑，覆盖"外部先忙、SSE 断连丢包"场景
      _ensurePolling();
    } else {
      _busySessions.remove(sessionId);
      _serverBusySeen.remove(sessionId);
      // 会话空闲时清掉挂起的确认卡
      pendingPermission = null;
      pendingQuestion = null;
      if (_busySessions.isEmpty) {
        // 完成后兜底刷新：无论本端还是终端触发，最终状态以服务端为准拉齐
        final active = activeSessionId;
        if (active != null) {
          unawaited(_loadMessages(active));
        }
        unawaited(_refreshSessionsOnly());
      }
    }
    notifyListeners();
  }

  /// 切换 Plan ⇄ Build（Tab / 左下角按钮）。供发起消息时作为 agent 覆盖。
  void setAgentMode(String mode) {
    if (mode != 'build' && mode != 'plan') return;
    if (agentMode == mode) {
      notifyListeners();
      return;
    }
    agentMode = mode;
    notifyListeners();
  }

  void toggleAgentMode() {
    setAgentMode(planMode ? 'build' : 'plan');
  }

  Future<void> healthCheck() async {
    final health = await _client.health();
    final old = serverReachable;
    serverReachable = health?.healthy ?? false;
    serverVersion = health?.version;
    if (old != serverReachable) {
      notifyListeners();
    }
  }

  Future<void> refresh() async {
    loading = true;
    lastError = null;
    notifyListeners();
    try {
      final health = await _client.health();
      serverReachable = health?.healthy ?? false;
      serverVersion = health?.version;

      final models = await _client.listModels();
      this.models = models;
      if (selectedModelId == null ||
          !models.any((model) => model.id == selectedModelId)) {
        selectedModelId = _pickDefaultModel(models);
      }

      try {
        commands = await _client.listCommands();
      } catch (_) {
        // 命令列表拉取失败不影响主流程
      }

      final sessions = await _client.listSessions();
      this.sessions = sessions;
      final roots = rootSessions;
      if (roots.isNotEmpty &&
          (activeSessionId == null ||
              !roots.any((session) => session.id == activeSessionId))) {
        activeSessionId = roots.first.id;
      }
      if (activeSessionId != null) {
        await _loadMessages(activeSessionId!);
      } else {
        messages = const [];
      }
    } catch (error) {
      lastError = error.toString();
    } finally {
      loading = false;
      notifyListeners();
    }
  }

  void selectModel(String? modelId) {
    selectedModelId = modelId;
    notifyListeners();
  }

  Future<void> selectSession(String sessionId) async {
    activeSessionId = sessionId;
    try {
      await _loadMessages(sessionId);
    } catch (error) {
      lastError = error.toString();
    }
    notifyListeners();
  }

  Future<void> createSession() async {
    try {
      final session = await _client.createSession();
      activeSessionId = session.id;
      messages = const [];
      attachments = const [];
      final sessions = await _client.listSessions();
      this.sessions = sessions;
    } catch (error) {
      lastError = error.toString();
    }
    notifyListeners();
  }

  /// 删除会话及其全部子会话（级联）。返回 true 表示删除成功。
  Future<bool> deleteSession(String sessionId) async {
    try {
      final ids = _collectDescendants(sessionId);
      for (final id in ids) {
        await _client.deleteSession(id);
      }
      _busySessions.remove(sessionId);
      _ownPending.remove(sessionId);
      if (activeSessionId == sessionId) {
        activeSessionId = null;
        messages = const [];
        attachments = const [];
      }
      final sessions = await _client.listSessions();
      this.sessions = sessions;
      final roots = rootSessions;
      if (activeSessionId == null && roots.isNotEmpty) {
        activeSessionId = roots.first.id;
        await _loadMessages(activeSessionId!);
      } else if (activeSessionId == null) {
        messages = const [];
      }
      return true;
    } catch (error) {
      lastError = error.toString();
      return false;
    } finally {
      notifyListeners();
    }
  }

  List<String> _collectDescendants(String sessionId) {
    final byParent = <String, List<String>>{};
    for (final session in sessions) {
      final parent = session.parentId;
      if (parent == null) continue;
      byParent.putIfAbsent(parent, () => []).add(session.id);
    }
    final result = <String>[sessionId];
    var index = 0;
    while (index < result.length) {
      final children = byParent[result[index]] ?? const <String>[];
      result.addAll(children);
      index++;
    }
    return result;
  }

  /// 请求服务端压缩当前会话，成功后重新加载消息。
  Future<bool> compactSession() async {
    final sessionId = activeSessionId;
    if (sessionId == null) {
      lastError = '当前没有打开的会话';
      return false;
    }
    try {
      final ok = await _client.summarize(sessionId);
      if (ok) {
        await _loadMessages(sessionId);
        await _refreshSessionsOnly();
      }
      return ok;
    } catch (error) {
      lastError = error.toString();
      return false;
    }
  }

  /// 执行自定义命令（如 /init /review 等）。
  Future<bool> executeCommand(String command, [String arguments = '']) async {
    final sessionId = activeSessionId;
    if (sessionId == null) {
      lastError = '当前没有打开的会话';
      return false;
    }
    try {
      final ok = await _client.executeCommand(
        sessionId,
        command: command,
        arguments: arguments,
      );
      if (ok) {
        await _loadMessages(sessionId);
        await _refreshSessionsOnly();
      }
      return ok;
    } catch (error) {
      lastError = error.toString();
      return false;
    }
  }

  /// 发送文本（+ 附件），走 prompt_async 流式。agentMode 作为 agent 覆盖。
  ///
  /// 若目标会话正被别处（终端）占用且未指定 [takeover]，抛 [ExternalBusyException]，
  /// 由 UI 弹确认框后以 takeover: true 重试（将先 abort 终端任务再接管）。
  Future<bool> sendMessage(String text, {bool takeover = false}) async {
    if (activeSessionId == null) {
      lastError = null;
      try {
        final session = await _client.createSession();
        activeSessionId = session.id;
      } catch (error) {
        lastError = error.toString();
        notifyListeners();
        return false;
      }
    }
    final sessionId = activeSessionId!;
    if (_ownPending.contains(sessionId)) return false;
    if (externalBusyForActive && !takeover) {
      throw ExternalBusyException(sessionId);
    }
    return _dispatch(sessionId, text, takeover: takeover);
  }

  Future<bool> _dispatch(String sessionId, String text,
      {required bool takeover}) async {
    if (_busySessions.contains(sessionId) && !_ownPending.contains(sessionId)) {
      // 别处（终端/其他端）正在跑同一个会话：接管前先停掉再发（绕上游 bug #46842）
      if (takeover) {
        try {
          await _client.abortSession(sessionId);
        } catch (_) {}
      }
    }
    _ownPending.add(sessionId);
    _setBusy(sessionId, true);
    _lastOwnSendAt[sessionId] = DateTime.now();
    lastError = null;
    notifyListeners();
    try {
      final parts = <String, dynamic>{
        'type': 'text',
        'text': text,
      };
      final ids = selectedModelId?.split('/') ?? const <String>[];
      final providerId = ids.isNotEmpty ? ids[0] : null;
      final modelId = ids.length > 1 ? ids[1] : null;
      await _client.sendPromptAsync(
        sessionId,
        parts: [
          parts,
          for (final att in attachments) _filePart(att),
        ],
        providerId: providerId,
        modelId: modelId,
        agent: agentMode,
      );
      attachments = const [];
      _ensurePolling();
      return true;
    } on Exception catch (error) {
      _ownPending.remove(sessionId);
      _setBusy(sessionId, false);
      lastError = error.toString();
      return false;
    } finally {
      notifyListeners();
    }
  }

  Map<String, dynamic> _filePart(OpencodeAttachment attachment) {
    return {
      'type': 'file',
      'mime': attachment.mime,
      'url': Uri.file(attachment.path).toString(),
      'filename': attachment.name,
    };
  }

  /// 停止当前会话（abort）。
  Future<bool> stop() async {
    final sessionId = activeSessionId;
    if (sessionId == null) return false;
    try {
      final ok = await _client.abortSession(sessionId);
      _ownPending.remove(sessionId);
      _setBusy(sessionId, false);
      await _loadMessages(sessionId);
      await _refreshSessionsOnly();
      return ok;
    } catch (error) {
      lastError = error.toString();
      return false;
    }
  }

  void addAttachment(String path) {
    final name = path.split(RegExp(r'[\\/]')).last;
    if (attachments.any((att) => att.path == path)) {
      lastError = '文件已添加：$name';
      notifyListeners();
      return;
    }
    final mime = _guessMime(name);
    attachments = [...attachments, OpencodeAttachment(path: path, name: name, mime: mime)];
    notifyListeners();
  }

  void removeAttachment(int index) {
    if (index < 0 || index >= attachments.length) return;
    attachments = [...attachments]..removeAt(index);
    notifyListeners();
  }

  String _guessMime(String name) {
    final lower = name.toLowerCase();
    if (lower.endsWith('.md') || lower.endsWith('.txt')) return 'text/plain';
    if (lower.endsWith('.pdf')) return 'application/pdf';
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
    if (lower.endsWith('.gif')) return 'image/gif';
    if (lower.endsWith('.webp')) return 'image/webp';
    if (lower.endsWith('.json')) return 'application/json';
    if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'text/html';
    if (lower.endsWith('.css')) return 'text/css';
    if (lower.endsWith('.csv')) return 'text/csv';
    if (lower.endsWith('.xml')) return 'application/xml';
    if (lower.endsWith('.zip')) return 'application/zip';
    if (lower.endsWith('.dart') ||
        lower.endsWith('.ts') ||
        lower.endsWith('.js') ||
        lower.endsWith('.py') ||
        lower.endsWith('.rs') ||
        lower.endsWith('.cpp') ||
        lower.endsWith('.c') ||
        lower.endsWith('.go') ||
        lower.endsWith('.java') ||
        lower.endsWith('.kt') ||
        lower.endsWith('.swift') ||
        lower.endsWith('.sql')) {
      return 'text/plain';
    }
    return 'application/octet-stream';
  }

  /// 权限三类回复。
  Future<bool> replyPermission({
    required String decision,
    String? message,
  }) async {
    final request = pendingPermission;
    if (request == null) return false;
    final ok = await _client.replyPermission(
      request.id,
      reply: decision,
      message: message,
    );
    if (ok) {
      pendingPermission = null;
      _refreshStatusesSoon();
    }
    notifyListeners();
    return ok;
  }

  /// 提交整组选择题答案（顺序对应各步骤）。
  Future<bool> answerQuestion(List<List<String>> answers) async {
    final request = pendingQuestion;
    if (request == null) return false;
    final ok = await _client.replyQuestion(request.id, answers);
    if (ok) {
      pendingQuestion = null;
      _refreshStatusesSoon();
    }
    notifyListeners();
    return ok;
  }

  /// 跳过当前问题组。
  Future<bool> skipQuestion() async {
    final request = pendingQuestion;
    if (request == null) return false;
    final ok = await _client.rejectQuestion(request.id);
    if (ok) {
      pendingQuestion = null;
      _refreshStatusesSoon();
    }
    notifyListeners();
    return ok;
  }

  void _refreshStatusesSoon() {
    Future<void>.delayed(const Duration(milliseconds: 300), () async {
      final statuses = await _client.getSessionStatuses();
      _reconcileStatuses(statuses);
    });
  }

  /// 兜底轮询：1s 一次，init 后常驻。
  /// busy/待回复时会话每 tick 拉消息保持 UI 同步；全部空闲时只做廉价的
  /// /session/status 检查，保证"外部先忙、SSE 断连丢包"的 idle→busy
  /// 转变也能在 1s 内被发现（轮询自停会永久错过该场景）。
  void _ensurePolling() {
    _pollTimer ??= Timer.periodic(const Duration(seconds: 1), (_) {
      unawaited(_pollTickRun());
    });
  }

  Future<void> _pollTickRun() async {
    _pollTick++;
    var statuses = <String, String>{};
    try {
      statuses = await _client.getSessionStatuses();
    } catch (_) {
      return; // server 不可达：健康横幅由 8s health 检查负责
    }
    _reconcileStatuses(statuses);

    final active = activeSessionId;
    if (active == null) return;
    final activeBusy = _busySessions.contains(active) ||
        _ownPending.contains(active);
    if (activeBusy) {
      await _loadMessages(active);
      if (_pollTick % 4 == 0) {
        await _refreshSessionsOnly();
      }
    } else if (_pollTick % 8 == 0) {
      // 全空闲时轻量同步会话列表（发现外部新建会话等）
      await _refreshSessionsOnly();
    }
  }

  void _reconcileStatuses(Map<String, String> statuses) {
    for (final entry in statuses.entries) {
      final busy = entry.value != 'idle';
      final had = _busySessions.contains(entry.key);
      if (busy && !had) {
        _setBusy(entry.key, true);
      } else if (!busy && had && _serverBusySeen.contains(entry.key)) {
        // 只信真实 busy→idle 转变（busy 由 SSE/轮询经 _setBusy 记入）：
        // 陈旧 idle 快照（本端发送刚发出、服务端尚未置 busy 时采到）不清
        // ownPending，否则 stop 按钮会消失
        final lastSend = _lastOwnSendAt[entry.key];
        final withinSendWindow =
            lastSend != null &&
            DateTime.now().difference(lastSend) < const Duration(seconds: 3);
        if (withinSendWindow) continue;
        _ownPending.remove(entry.key);
        _setBusy(entry.key, false);
      }
    }
  }

  String? _pickDefaultModel(List<OpencodeModel> models) {
    if (models.isEmpty) return null;
    for (final model in models) {
      if (model.isDefault) return model.id;
    }
    return models.first.id;
  }

  Future<void> _loadMessages(String sessionId) async {
    final fresh = await _client.listMessages(sessionId);
    if (activeSessionId != sessionId) return;
    if (!_sameMessages(fresh, messages)) {
      messages = fresh;
      notifyListeners();
    }
  }

  bool _sameMessages(List<OpencodeMessage> a, List<OpencodeMessage> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i].id != b[i].id || a[i].content != b[i].content) return false;
    }
    return true;
  }

  Future<void> _refreshSessionsOnly() async {
    try {
      final sessions = await _client.listSessions();
      this.sessions = sessions;
      notifyListeners();
    } catch (_) {}
  }

  @override
  void dispose() {
    _healthTimer?.cancel();
    _pollTimer?.cancel();
    _eventSub?.cancel();
    _permissionSub?.cancel();
    _questionSub?.cancel();
    _client.dispose();
    super.dispose();
  }
}