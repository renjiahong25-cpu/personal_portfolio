import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

/// 内存会话：状态 + 消息列表，序列化形状对齐真实 opencode server。
class MockSession {
  MockSession({
    required this.id,
    required this.title,
    this.parentId,
    this.agent = 'build',
  });

  final String id;
  String title;
  final String? parentId;
  String? agent;
  String status = 'idle';
  DateTime createdAt = DateTime.now();
  DateTime updatedAt = DateTime.now();
  final List<Map<String, dynamic>> messages = [];

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'directory': 'mock://$id',
        if (parentId != null) 'parentID': parentId,
        'agent': agent,
        'time': {
          'created': createdAt.millisecondsSinceEpoch,
          'updated': updatedAt.millisecondsSinceEpoch,
        },
      };
}

/// 实现 opencode server 的 HTTP 契约 + SSE 事件流，供集成测试替代真实 4096 服务。
///
/// 测试可用 [setStatus]/[insertMessage]/[idle] 模拟"终端侧"行为，并可断言
/// [promptAsyncCalls]/[abortCalls]/[summarizeCalls] 等记录来核对 UI 操作。
class MockOpencodeServer {
  MockOpencodeServer({this.autoReply = true, this.autoReplyText});

  final bool autoReply;
  final String? autoReplyText;

  HttpServer? _server;
  final List<MockSession> sessionList = [];

  /// 断言用记录。
  final List<Map<String, dynamic>> promptAsyncCalls = [];
  final List<String> abortCalls = [];
  final List<String> summarizeCalls = [];
  final List<String> deletedSessions = [];
  final List<Map<String, dynamic>> permissionReplies = [];
  final List<Map<String, dynamic>> questionReplies = [];
  final List<String> rejectedQuestions = [];
  final List<Map<String, dynamic>> commands = [];

  int _seq = 0;
  final StreamController<String> _eventBus =
      StreamController<String>.broadcast();
  final List<StreamSubscription<String>> _sseSubs = [];

  String _nextId(String prefix) => '$prefix-${++_seq}';

  /// 绑定随机端口并开始服务，返回实际端口。
  Future<int> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server!.listen(_handle);
    return _server!.port;
  }

  Future<void> stop() async {
    for (final sub in _sseSubs) {
      await sub.cancel();
    }
    _sseSubs.clear();
    await _server?.close(force: true);
    _server = null;
  }

  // ----- 会话/消息便捷 API -----

  MockSession? session(String id) {
    for (final s in sessionList) {
      if (s.id == id) return s;
    }
    return null;
  }

  MockSession seedSession(
    String id, {
    String title = '未命名会话',
    String? parentId,
    String agent = 'build',
  }) {
    final s = MockSession(id: id, title: title, parentId: parentId, agent: agent);
    sessionList.add(s);
    return s;
  }

  /// 暴露/调整某会话的外部忙态（模拟终端开始/结束任务），并广播对应事件。
  void setStatus(String sessionId, String status) {
    final s = session(sessionId);
    if (s == null) return;
    s.status = status;
    emitEvent('session.status', {
      'sessionID': sessionId,
      'status': {'type': status},
    });
  }

  void idle(String sessionId) {
    setStatus(sessionId, 'idle');
    emitEvent('session.idle', {'sessionID': sessionId});
  }

  /// 向某会话插入一条消息并广播 message.updated + session.updated。
  void insertMessage(
    String sessionId, {
    required String role,
    String? text,
    List<Map<String, dynamic>>? fileParts,
    String providerId = 'deepseek',
    String modelId = 'deepseek-chat',
    String? agent,
  }) {
    final s = session(sessionId);
    if (s == null) return;
    final now = DateTime.now();
    final message = <String, dynamic>{
      'info': <String, dynamic>{
        'id': _nextId('m'),
        'role': role,
        'time': {'created': now.millisecondsSinceEpoch},
        'providerID': providerId,
        'modelID': modelId,
        'agent': ?agent,
      },
      'parts': <Map<String, dynamic>>[
        ...?fileParts,
        if (text != null && text.isNotEmpty) {'type': 'text', 'text': text},
      ],
    };
    s.messages.add(message);
    s.updatedAt = now;
    emitEvent('message.updated', {'sessionID': sessionId, 'message': message});
    emitEvent('session.updated', {'sessionID': sessionId});
  }

  /// 终端侧完整一轮：busy → 追加用户+助手消息 → idle。
  Future<void> terminalRun(
    String sessionId,
    String userText, {
    String reply = '（终端回复）收到。',
    Duration stepDelay = const Duration(milliseconds: 50),
    List<Map<String, dynamic>>? replyFileParts,
  }) async {
    setStatus(sessionId, 'busy');
    insertMessage(sessionId, role: 'user', text: userText);
    await Future<void>.delayed(stepDelay);
    insertMessage(
      sessionId,
      role: 'assistant',
      text: reply,
      fileParts: replyFileParts,
    );
    // 任务保持 busy 足够久（模拟真实耗时），确保控制器 1s 兜底轮询
    // 能采到 busy 状态并在窗口内拉到完整消息
    await Future<void>.delayed(const Duration(milliseconds: 2500));
    idle(sessionId);
  }

  /// 助手侧单轮：busy → 追加 assistant 消息（可带 file parts）→ idle。
  /// 模拟真实生成：产出文件/图表期间会话处于 busy，保证兜底轮询能拉到消息。
  Future<void> assistantRun(
    String sessionId, {
    required String text,
    List<Map<String, dynamic>>? fileParts,
    Duration busyWindow = const Duration(milliseconds: 2500),
  }) async {
    setStatus(sessionId, 'busy');
    insertMessage(
      sessionId,
      role: 'assistant',
      text: text,
      fileParts: fileParts,
    );
    await Future<void>.delayed(busyWindow);
    idle(sessionId);
  }

  void emitEvent(String type, Map<String, dynamic> properties) {
    debugPrint(
      '[mock-sse] emit $type 监听器=${_sseSubs.length} bus有订阅=${_eventBus.hasListener}',
    );
    _eventBus.add(
      'data: ${jsonEncode({
            'id': _nextId('e'),
            'type': type,
            'properties': properties,
          })}\n\n',
    );
  }

  // ----- 内部：HTTP 路由 -----

  Future<void> _handle(HttpRequest request) async {
    final path = request.uri.path;
    final method = request.method;
    try {
      if (method == 'GET' && path == '/event') {
        await _handleSse(request);
        return;
      }
      if (method == 'GET' && path == '/global/health') {
        await _json(request, 200, {'healthy': true, 'version': 'mock'});
        return;
      }
      if (method == 'GET' && path == '/config/providers') {
        await _json(request, 200, {
          'providers': [
            {
              'id': 'deepseek',
              'name': 'DeepSeek',
              'models': {
                'deepseek-chat': {'id': 'deepseek-chat', 'name': 'deepseek-chat'},
                'deepseek-r1': {'id': 'deepseek-r1', 'name': 'deepseek-r1'},
              },
            },
          ],
          'default': {'deepseek': 'deepseek-chat'},
        });
        return;
      }
      if (method == 'GET' && path == '/command') {
        await _json(request, 200, <Object>[]);
        return;
      }
      if (method == 'GET' && path == '/session') {
        final json = sessionList
            .map((s) => s.toJson())
            .toList()
          ..sort((a, b) {
            final at = (a['time'] as Map)['updated'] as int;
            final bt = (b['time'] as Map)['updated'] as int;
            return bt.compareTo(at);
          });
        await _json(request, 200, json);
        return;
      }
      if (method == 'POST' && path == '/session') {
        final body = await _readBody(request);
        final title = (body as Map?)?['title'] as String? ?? '未命名会话';
        final s = MockSession(id: _nextId('s'), title: title);
        sessionList.add(s);
        emitEvent('session.created', {'sessionID': s.id});
        await _json(request, 200, s.toJson());
        return;
      }
      if (method == 'PATCH' && RegExp(r'^/session/[^/]+$').hasMatch(path)) {
        final id = _segment(path, 2);
        final body = await _readBody(request);
        final s = session(id);
        if (s == null) {
          await _json(request, 404, {'error': 'no session'});
          return;
        }
        final title = (body as Map?)?['title'] as String?;
        if (title != null) s.title = title;
        await _json(request, 200, s.toJson());
        return;
      }
      if (method == 'DELETE' && RegExp(r'^/session/[^/]+$').hasMatch(path)) {
        final id = _segment(path, 2);
        deletedSessions.add(id);
        sessionList.removeWhere((s) => s.id == id);
        emitEvent('session.deleted', {'sessionID': id});
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'GET' &&
          RegExp(r'^/session/[^/]+/message$').hasMatch(path)) {
        final id = _segment(path, 2);
        final s = session(id);
        await _json(request, 200, s?.messages ?? <Object>[]);
        return;
      }
      if (method == 'GET' && path == '/session/status') {
        final statuses = <String, dynamic>{};
        for (final s in sessionList) {
          statuses[s.id] = {'type': s.status};
        }
        await _json(request, 200, statuses);
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/session/[^/]+/prompt_async$').hasMatch(path)) {
        final id = _segment(path, 2);
        final body = await _readBody(request);
        final map = body is Map<String, dynamic> ? body : <String, dynamic>{};
        final parts = [...(map['parts'] as List? ?? const []).whereType<Map>()];
        final model = map['model'];
        final modelMap = model is Map ? model : const <String, dynamic>{};
        promptAsyncCalls.add({
          'sessionId': id,
          'parts': parts,
          'providerId': modelMap['providerID'],
          'modelId': modelMap['modelID'],
          'agent': map['agent'],
        });
        final text = parts
            .map((p) => p['text'] as String? ?? '')
            .where((t) => t.isNotEmpty)
            .join('\n');
        insertMessage(id, role: 'user', text: text);
        final s = session(id);
        if (s != null) s.status = 'responding';
        if (autoReply) {
          unawaited(_autoReply(id, text));
        }
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/session/[^/]+/abort$').hasMatch(path)) {
        final id = _segment(path, 2);
        abortCalls.add(id);
        final s = session(id);
        if (s != null) s.status = 'idle';
        emitEvent('session.idle', {'sessionID': id});
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/session/[^/]+/summarize$').hasMatch(path)) {
        final id = _segment(path, 2);
        summarizeCalls.add(id);
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/session/[^/]+/command$').hasMatch(path)) {
        final id = _segment(path, 2);
        final body = await _readBody(request);
        commands.add({
          'sessionId': id,
          ...(body is Map ? Map<String, dynamic>.from(body) : const {}),
        });
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/permission/[^/]+/reply$').hasMatch(path)) {
        final id = _segment(path, 2);
        final body = await _readBody(request);
        permissionReplies.add({
          'id': id,
          ...(body is Map ? Map<String, dynamic>.from(body) : const {}),
        });
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/question/[^/]+/reply$').hasMatch(path)) {
        final id = _segment(path, 2);
        final body = await _readBody(request);
        questionReplies.add({
          'id': id,
          ...(body is Map ? Map<String, dynamic>.from(body) : const {}),
        });
        await _rawJson(request, 200, 'true');
        return;
      }
      if (method == 'POST' &&
          RegExp(r'^/question/[^/]+/reject$').hasMatch(path)) {
        final id = _segment(path, 2);
        rejectedQuestions.add(id);
        await _rawJson(request, 200, 'true');
        return;
      }
      await _json(request, 404, {'error': 'mock: not found $method $path'});
    } catch (error) {
      await _json(request, 500, {'error': error.toString()});
    }
  }

  Future<void> _autoReply(String sessionId, String userText) async {
    // 与测试节奏解耦：极短延迟，让 busy 先落地再出回复
    await Future<void>.delayed(const Duration(milliseconds: 30));
    if (session(sessionId) == null) return;
    insertMessage(
      sessionId,
      role: 'assistant',
      text: autoReplyText ?? '（mock 回复）已收到你的消息。',
    );
    await Future<void>.delayed(const Duration(milliseconds: 30));
    if (_server == null) return;
    idle(sessionId);
  }

  Future<void> _handleSse(HttpRequest request) async {
    debugPrint('[mock-sse] 新 SSE 客户端连接（保持打开，不推送事件）');
    final response = request.response;
    response.headers.set(HttpHeaders.contentTypeHeader, 'text/event-stream');
    response.headers.set(HttpHeaders.cacheControlHeader, 'no-cache');
    response.headers.set(HttpHeaders.connectionHeader, 'keep-alive');
    // 本测试环境 chunked 响应体无法送达客户端（dart:io 限制），事件实际由
    // 控制器 1s REST 兜底轮询同步。这里只发一个 SSE 注释提交 headers 并保持
    // 连接打开、不推送事件数据——向 socket 推送只会堆积发送缓冲 backpressure，
    // 干扰 app 主 isolate。客户端连接后阻塞等待属预期。
    response.write(': mock-sse open\n\n');
    await request.response.done;
  }

  String _segment(String path, int index) =>
      path.split('/').where((s) => s.isNotEmpty).toList()[index - 1];

  Future<Object?> _readBody(HttpRequest request) async {
    final text = await utf8.decoder.bind(request).join();
    if (text.isEmpty) return null;
    return jsonDecode(text);
  }

  Future<void> _rawJson(HttpRequest request, int status, String body) async {
    request.response.statusCode = status;
    request.response.headers.contentType = ContentType.json;
    request.response.write(body);
    await request.response.close();
  }

  Future<void> _json(HttpRequest request, int status, Object? body) =>
      _rawJson(request, status, jsonEncode(body ?? <String, dynamic>{}));
}