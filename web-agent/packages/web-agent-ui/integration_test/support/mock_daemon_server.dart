import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// 实现 web-agent 守护进程的 HTTP 契约（/ping /status /command），
/// 供集成测试替代真实 19305 端口，并支持翻转 extensionConnected、
/// 记录全部 action 以断言 UI 操作。
class MockDaemonServer {
  bool extensionConnected = true;
  String extensionVersion = '1.0.0-mock';
  int pending = 0;

  /// 助手回复内容模板（每个 targeted role 一条）。
  String replyContent = '（守护进程 mock 回复）收到你的任务。';

  HttpServer? _server;

  final List<Map<String, dynamic>> commands = [];

  /// chatId -> 已添加角色
  final Map<String, List<Map<String, dynamic>>> rolesByChat = {};
  final List<String> chats = [];
  int _taskSeq = 0;

  /// 绑定随机端口并返回实际端口。
  Future<int> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server!.listen(_handle);
    return _server!.port;
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _handle(HttpRequest request) async {
    final path = request.uri.path;
    final method = request.method;
    try {
      if (method == 'GET' && path == '/ping') {
        await _json(request, 200, 'pong');
        return;
      }
      if (method == 'GET' && path == '/status') {
        await _json(request, 200, {
          'reachable': true,
          'extensionConnected': extensionConnected,
          'extensionVersion': extensionVersion,
          'pending': pending,
        });
        return;
      }
      if (method == 'POST' && path == '/command') {
        final body = await _readBody(request);
        final map = body is Map<String, dynamic> ? body : <String, dynamic>{};
        final action = map['action'] as String? ?? '';
        final rawPayload = map['payload'];
        final payload = Map<String, dynamic>.from(
          rawPayload is Map ? rawPayload : const <String, dynamic>{},
        );
        commands.add({
          'action': action,
          'payload': Map<String, dynamic>.from(payload),
        });
        await _dispatch(request, action, payload);
        return;
      }
      await _json(request, 404, {'error': 'mock daemon: not found'});
    } catch (error) {
      await _json(request, 500, {'error': error.toString()});
    }
  }

  Future<void> _dispatch(
    HttpRequest request,
    String action,
    Map<String, dynamic> payload,
  ) async {
    switch (action) {
      case 'chat.create':
        final chatId = 'mock-chat-${chats.length + 1}';
        chats.add(chatId);
        rolesByChat[chatId] = [];
        await _ok(request, {
          'chat': {'id': chatId, 'name': payload['name'], 'mode': payload['mode']},
        });
        return;
      case 'chat.get':
        final chatId = payload['chatId'] as String? ?? '';
        await _ok(request, {'chat': {'id': chatId}, 'roles': rolesByChat[chatId] ?? const []});
        return;
      case 'roles.batchAdd':
        final chatId = payload['chatId'] as String? ?? '';
        final roles = rolesByChat.putIfAbsent(chatId, () => []);
        final items = (payload['items'] as List? ?? const []).whereType<Map>();
        final added = <Map<String, dynamic>>[];
        for (final item in items) {
          final site = item['chatSite'] as String? ?? 'deepseek';
          added.add({
            'id': 'mock-role-$site',
            'name': item['name'],
            'chatSite': site,
            'source': item['source'],
          });
        }
        roles.addAll(added);
        await _ok(request, {'chatId': chatId, 'roles': roles});
        return;
      case 'chat.initialize':
        await _ok(request, {'status': 'ready'});
        return;
      case 'task.post':
        final chatId = payload['chatId'] as String? ?? '';
        final messageId = 'mock-msg-${++_taskSeq}';
        final targetRoles = _targetRoles(payload['target']);
        final replies = targetRoles.map((role) {
          return {
            'messageId': 'mock-reply-$_taskSeq-${role['id']}',
            'roleId': role['id'],
            'roleName': role['name'] ?? role['chatSite'],
            'content': replyContent,
            'status': 'done',
            'conversationUrl': null,
          };
        }).toList();
        await _ok(request, {
          'chatId': chatId,
          'messageId': messageId,
          'replies': replies,
          'pendingRoleIds': <String>[],
          'errorRoleIds': <String>[],
        });
        return;
      case 'task.read':
      case 'task.wait':
        await _ok(request, {
          'chatId': payload['chatId'],
          'messageId': payload['messageId'],
          'replies': <Map<String, dynamic>>[],
          'pendingRoleIds': <String>[],
          'errorRoleIds': <String>[],
        });
        return;
      case 'task.stop':
        await _ok(request, {
          'chatId': payload['chatId'],
          'stopped': true,
        });
        return;
      default:
        await _json(request, 501, {
          'ok': false,
          'error': {'code': 'unknown_action', 'message': 'mock: 未知 action $action'},
        });
    }
  }

  List<Map<String, dynamic>> _targetRoles(Object? target) {
    final all = <Map<String, dynamic>>[];
    for (final roles in rolesByChat.values) {
      all.addAll(roles);
    }
    if (target is Map) {
      final ids = (target['roleIds'] as List? ?? const []).whereType<String>();
      return all.where((role) => ids.contains(role['id'])).toList();
    }
    return all;
  }

  Future<void> _ok(HttpRequest request, Map<String, dynamic> data) =>
      _json(request, 200, {'ok': true, 'data': data});

  Future<Object?> _readBody(HttpRequest request) async {
    final text = await utf8.decoder.bind(request).join();
    if (text.isEmpty) return null;
    return jsonDecode(text);
  }

  Future<void> _json(HttpRequest request, int status, Object? body) async {
    request.response.statusCode = status;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(body));
    await request.response.close();
  }
}