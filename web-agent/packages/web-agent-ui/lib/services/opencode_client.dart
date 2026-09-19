import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';

const kOpencodePort = 4096;

class OpencodeHealth {
  const OpencodeHealth({required this.healthy, this.version});

  final bool healthy;
  final String? version;
}

class OpencodeSessionData {
  const OpencodeSessionData({
    required this.id,
    required this.title,
    required this.directory,
    this.createdAt,
    this.updatedAt,
    this.parentId,
    this.agent,
  });

  final String id;
  final String title;
  final String directory;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  /// 若为子会话（subagent/子任务），指向父会话 id；顶层会话为 null。
  final String? parentId;

  /// 会话当前使用的 agent（如 build/plan/general）。
  final String? agent;
}

class OpencodeModel {
  const OpencodeModel({
    required this.providerId,
    required this.modelId,
    required this.name,
    required this.providerName,
    this.isDefault = false,
  });

  final String providerId;
  final String modelId;
  final String name;
  final String providerName;
  final bool isDefault;

  String get id => '$providerId/$modelId';

  String get label => '$providerName · $name';
}

/// 消息里引用的生成文件（message part 中 type:file 的引用）。
class OpencodeFileRef {
  const OpencodeFileRef({required this.path, required this.name, this.mime});

  final String path;
  final String name;
  final String? mime;
}

class OpencodeMessage {
  const OpencodeMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.createdAt,
    this.modelLabel,
    this.agent,
    this.files = const [],
  });

  final String id;
  final String role;
  final String content;
  final DateTime createdAt;
  final String? modelLabel;

  /// 消息所属的 agent（plan/build 判定用）。
  final String? agent;

  /// 消息中引用的生成文件（type:file part）。
  final List<OpencodeFileRef> files;

  OpencodeMessage copyWith({String? content}) {
    return OpencodeMessage(
      id: id,
      role: role,
      content: content ?? this.content,
      createdAt: createdAt,
      modelLabel: modelLabel,
      agent: agent,
      files: files,
    );
  }
}

class OpencodeCommand {
  const OpencodeCommand({required this.name, this.description});

  final String name;
  final String? description;
}

/// 权限回复可选项：一次 / 总是允许 / 拒绝。
abstract final class OpencodePermissionDecision {
  static const once = 'once';
  static const always = 'always';
  static const reject = 'reject';
}

/// /event SSE 推送的原始事件。
class OpencodeEvent {
  const OpencodeEvent({required this.id, required this.type, this.properties});

  final String id;
  final String type;
  final Map<String, dynamic>? properties;
}

/// permission.asked 事件对应的权限请求。
class OpencodePermissionRequest {
  const OpencodePermissionRequest({
    required this.id,
    required this.sessionId,
    required this.permission,
    required this.patterns,
    this.metadata,
    this.always = const [],
    this.tool,
  });

  final String id;
  final String sessionId;
  final String permission;
  final List<String> patterns;
  final Map<String, dynamic>? metadata;
  final List<String> always;

  /// {messageID, callID}：请求对应到哪个工具调用。
  final Map<String, dynamic>? tool;
}

class OpencodeQuestionOption {
  const OpencodeQuestionOption({required this.label, this.description});

  final String label;
  final String? description;
}

class OpencodeQuestionInfo {
  const OpencodeQuestionInfo({
    required this.header,
    required this.question,
    required this.options,
    this.multiple = false,
    this.custom = false,
  });

  final String header;

  /// 完整问题文本。
  final String question;

  /// 可选标签（label），第一个通常为推荐项。
  final List<OpencodeQuestionOption> options;
  final bool multiple;
  final bool custom;
}

/// question.asked 事件对应的整组选择题请求（数组即多步骤）。
class OpencodeQuestionRequest {
  const OpencodeQuestionRequest({
    required this.id,
    required this.sessionId,
    required this.questions,
  });

  final String id;
  final String sessionId;
  final List<OpencodeQuestionInfo> questions;
}

class OpencodeClient {
  OpencodeClient({String? baseUrl, Duration? receiveTimeout})
    : _baseUrl = baseUrl ?? 'http://127.0.0.1:$kOpencodePort',
      _dio = Dio(
        BaseOptions(
          baseUrl: baseUrl ?? 'http://127.0.0.1:$kOpencodePort',
          connectTimeout: const Duration(seconds: 3),
          receiveTimeout: receiveTimeout ?? const Duration(minutes: 10),
        ),
      );

  final String _baseUrl;
  final Dio _dio;

  StreamController<OpencodeEvent>? _eventCtrl;
  bool _eventClosed = false;

  /// /event SSE 事件流（自动重连）。首次访问时启动连接。
  Stream<OpencodeEvent> get events {
    final existing = _eventCtrl;
    if (existing != null) return existing.stream;
    final ctrl = StreamController<OpencodeEvent>.broadcast(
      onListen: _startEventLoop,
    );
    _eventCtrl = ctrl;
    return ctrl.stream;
  }

  /// permission.asked 事件（带会话过滤由调用方处理）。
  Stream<OpencodePermissionRequest> get permissionRequests => events
      .where((event) => event.type == 'permission.asked')
      .map((event) => _parsePermission(event.properties));

  /// question.asked 事件。
  Stream<OpencodeQuestionRequest> get questionRequests => events
      .where((event) => event.type == 'question.asked')
      .map((event) => _parseQuestion(event.properties));

  /// 断开并清理 SSE 连接。应用退出时调用。
  void dispose() {
    _eventClosed = true;
    _eventCtrl?.close();
    _eventCtrl = null;
  }

  Future<void> _startEventLoop() async {
    while (!_eventClosed) {
      try {
        final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
        final request = await client.getUrl(Uri.parse('$_baseUrl/event'));
        request.headers.set(HttpHeaders.acceptHeader, 'text/event-stream');
        final response = await request.close();
        if (response.statusCode == 200) {
          await _drainEvent(response);
        } else {
          await response.drain<void>();
        }
        client.close(force: true);
        if (_eventClosed) return;
        await Future<void>.delayed(const Duration(milliseconds: 1500));
      } catch (_) {
        if (_eventClosed) return;
        await Future<void>.delayed(const Duration(seconds: 3));
      }
    }
  }

  Future<void> _drainEvent(HttpClientResponse response) async {
    final leftover = StringBuffer();
    await for (final chunk in response) {
      leftover.write(utf8.decode(chunk, allowMalformed: true));
      final text = leftover.toString();
      if (text.isEmpty) continue;
      final blocks = text.split('\n\n');
      if (blocks.length > 1) {
        for (final block in blocks.take(blocks.length - 1)) {
          final event = _parseSseEvent(block);
          if (event != null) {
            _eventCtrl?.add(event);
          }
        }
        leftover.clear();
        leftover.write(blocks.last);
      }
    }
  }

  OpencodeEvent? _parseSseEvent(String block) {
    final dataLines = <String>[];
    for (final line in block.split('\n')) {
      if (line.startsWith('data:')) {
        dataLines.add(line.substring(5).trim());
      }
    }
    if (dataLines.isEmpty) return null;
    try {
      final decoded = jsonDecode(dataLines.join('\n'));
      if (decoded is! Map) return null;
      return OpencodeEvent(
        id: decoded['id'] as String? ?? '',
        type: decoded['type'] as String? ?? '',
        properties: (decoded['properties'] as Map?)?.cast<String, dynamic>(),
      );
    } catch (_) {
      return null;
    }
  }

  OpencodePermissionRequest _parsePermission(Map<String, dynamic>? props) {
    final p = props ?? const <String, dynamic>{};
    return OpencodePermissionRequest(
      id: p['id'] as String? ?? '',
      sessionId: p['sessionID'] as String? ?? '',
      permission: p['permission'] as String? ?? '',
      patterns: [...(p['patterns'] as List? ?? const []).whereType<String>()],
      metadata: (p['metadata'] as Map?)?.cast<String, dynamic>(),
      always: [...(p['always'] as List? ?? const []).whereType<String>()],
      tool: (p['tool'] as Map?)?.cast<String, dynamic>(),
    );
  }

  OpencodeQuestionRequest _parseQuestion(Map<String, dynamic>? props) {
    final p = props ?? const <String, dynamic>{};
    final questions = <OpencodeQuestionInfo>[];
    final rawQuestions = p['questions'];
    if (rawQuestions is List) {
      for (final raw in rawQuestions.whereType<Map>()) {
        final options = <OpencodeQuestionOption>[];
        final rawOptions = raw['options'];
        if (rawOptions is List) {
          for (final option in rawOptions.whereType<Map>()) {
            options.add(
              OpencodeQuestionOption(
                label: option['label'] as String? ?? '',
                description: option['description'] as String?,
              ),
            );
          }
        }
        questions.add(
          OpencodeQuestionInfo(
            header: raw['header'] as String? ?? '',
            question: raw['question'] as String? ?? '',
            options: options,
            multiple: raw['multiple'] == true,
            custom: raw['custom'] == true,
          ),
        );
      }
    }
    return OpencodeQuestionRequest(
      id: p['id'] as String? ?? '',
      sessionId: p['sessionID'] as String? ?? '',
      questions: questions,
    );
  }

  Future<OpencodeHealth?> health() async {
    try {
      final response = await _dio.get<Map<String, dynamic>>('/global/health');
      final body = response.data;
      if (response.statusCode != 200 || body == null) return null;
      return OpencodeHealth(
        healthy: body['healthy'] == true,
        version: body['version'] as String?,
      );
    } catch (_) {
      return null;
    }
  }

  Future<List<OpencodeSessionData>> listSessions() async {
    final response = await _dio.get<List<dynamic>>('/session');
    final raw = response.data ?? const [];
    final sessions = <OpencodeSessionData>[];
    for (final item in raw) {
      if (item is! Map) continue;
      sessions.add(_parseSession(item));
    }
    sessions.sort((a, b) {
      final at = a.updatedAt?.millisecondsSinceEpoch ?? 0;
      final bt = b.updatedAt?.millisecondsSinceEpoch ?? 0;
      return bt.compareTo(at);
    });
    return sessions;
  }

  Future<OpencodeSessionData> createSession({String? title}) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '/session',
      data: title == null ? null : {'title': title},
    );
    if (response.data == null) {
      throw StateError('创建会话失败：无响应');
    }
    return _parseSession(response.data!);
  }

  Future<bool> deleteSession(String sessionId) async {
    final response = await _dio.delete<bool>('/session/$sessionId');
    return response.data == true;
  }

  Future<void> patchTitle(String sessionId, String title) async {
    await _dio.patch<Map<String, dynamic>>(
      '/session/$sessionId',
      data: {'title': title},
    );
  }

  Future<List<OpencodeMessage>> listMessages(String sessionId) async {
    final response = await _dio.get<List<dynamic>>(
      '/session/$sessionId/message',
    );
    final raw = response.data ?? const [];
    final messages = <OpencodeMessage>[];
    for (final item in raw) {
      if (item is! Map) continue;
      final info = item['info'];
      final parts = item['parts'];
      if (info is! Map) continue;
      final role = info['role'] as String? ?? 'assistant';
      final createdMs = _asInt((info['time'] as Map?)?['created']);
      final providerID = info['providerID'] as String?;
      final modelID = info['modelID'] as String?;
      final agent = info['agent'] as String?;
      var content = '';
      final files = <OpencodeFileRef>[];
      if (parts is List) {
        for (final part in parts.whereType<Map>()) {
          final type = part['type'];
          if (type == 'text') {
            final text = part['text'] as String? ?? '';
            if (text.isNotEmpty) {
              content = content.isEmpty ? text : '$content\n$text';
            }
          } else if (type == 'file') {
            final rawPath = (part['path'] as String?) ?? (part['url'] as String?);
            if (rawPath != null && rawPath.isNotEmpty) {
              final path = _normalizePath(rawPath);
              files.add(
                OpencodeFileRef(
                  path: path,
                  name: path.split(RegExp(r'[\\/]')).last,
                  mime: part['mime'] as String?,
                ),
              );
            }
          }
        }
      }
      messages.add(
        OpencodeMessage(
          id: info['id'] as String? ?? '',
          role: role,
          content: content,
          createdAt: DateTime.fromMillisecondsSinceEpoch(createdMs ?? 0),
          modelLabel: providerID != null && modelID != null
              ? '$providerID/$modelID'
              : null,
          agent: agent,
          files: List.unmodifiable(files),
        ),
      );
    }
    messages.sort((a, b) => a.createdAt.compareTo(b.createdAt));
    return messages;
  }

  Future<Map<String, dynamic>> sendMessage(
    String sessionId, {
    required String text,
    String? providerId,
    String? modelId,
  }) async {
    final body = <String, dynamic>{
      'parts': [
        {'type': 'text', 'text': text},
      ],
      if (providerId != null && modelId != null)
        'model': {'providerID': providerId, 'modelID': modelId},
    };
    final response = await _dio.post<Map<String, dynamic>>(
      '/session/$sessionId/message',
      data: body,
    );
    return response.data ?? const <String, dynamic>{};
  }

  /// 异步发送（流式），返回即 204/accept。agent 可覆盖会话的 agent（plan/build）。
  Future<void> sendPromptAsync(
    String sessionId, {
    required List<Map<String, dynamic>> parts,
    String? providerId,
    String? modelId,
    String? agent,
  }) async {
    final body = <String, dynamic>{
      'parts': parts,
      if (providerId != null && modelId != null)
        'model': {'providerID': providerId, 'modelID': modelId},
      if (agent != null && agent.isNotEmpty) 'agent': agent,
    };
    await _dio.post<dynamic>(
      '/session/$sessionId/prompt_async',
      data: body,
    );
  }

  /// 回复权限请求。reply 取值 once/always/reject，message 可选理由。
  Future<bool> replyPermission(
    String requestId, {
    required String reply,
    String? message,
  }) async {
    final body = <String, dynamic>{
      'reply': reply,
      if (message != null && message.trim().isNotEmpty)
        'message': message.trim(),
    };
    final response = await _dio.post<dynamic>(
      '/permission/$requestId/reply',
      data: body,
    );
    return response.statusCode != null &&
        response.statusCode! >= 200 &&
        response.statusCode! < 300;
  }

  /// 回复问题组：answers 顺序对应每道题（每题内为所选 label 列表）。
  Future<bool> replyQuestion(
    String requestId,
    List<List<String>> answers,
  ) async {
    final response = await _dio.post<dynamic>(
      '/question/$requestId/reply',
      data: {'answers': answers},
    );
    return response.statusCode != null &&
        response.statusCode! >= 200 &&
        response.statusCode! < 300;
  }

  /// 跳过当前问题组。
  Future<bool> rejectQuestion(String requestId) async {
    final response = await _dio.post<dynamic>('/question/$requestId/reject');
    return response.statusCode != null &&
        response.statusCode! >= 200 &&
        response.statusCode! < 300;
  }

  /// 会话状态快照：{sessionID: 'idle'|'busy'|'retry'}。做 busy 兜底对齐。
  Future<Map<String, String>> getSessionStatuses() async {
    try {
      final response = await _dio.get<Map<String, dynamic>>('/session/status');
      final body = response.data ?? const <String, dynamic>{};
      final result = <String, String>{};
      body.forEach((sessionId, status) {
        if (status is Map) {
          final type = status['type'] as String?;
          if (type != null) result[sessionId] = type;
        } else if (status is String) {
          result[sessionId] = status;
        }
      });
      return result;
    } catch (_) {
      return const <String, String>{};
    }
  }

  Future<bool> abortSession(String sessionId) async {
    final response = await _dio.post<bool>('/session/$sessionId/abort');
    return response.data == true;
  }

  /// 请求服务端压缩会话（summarize），返回是否成功。
  Future<bool> summarize(String sessionId) async {
    final response = await _dio.post<bool>('/session/$sessionId/summarize');
    return response.data == true;
  }

  /// 执行自定义命令（如 init/review/http 等），返回是否成功。
  Future<bool> executeCommand(
    String sessionId, {
    required String command,
    String arguments = '',
  }) async {
    await _dio.post<Map<String, dynamic>>(
      '/session/$sessionId/command',
      data: {'command': command, 'arguments': arguments},
    );
    return true;
  }

  Future<List<OpencodeCommand>> listCommands() async {
    final response = await _dio.get<List<dynamic>>('/command');
    final raw = response.data ?? const [];
    final commands = <OpencodeCommand>[];
    for (final item in raw) {
      if (item is! Map) continue;
      commands.add(
        OpencodeCommand(
          name: item['name'] as String? ?? '',
          description: item['description'] as String?,
        ),
      );
    }
    return commands;
  }

  Future<List<OpencodeModel>> listModels() async {
    final response = await _dio.get<Map<String, dynamic>>('/config/providers');
    final body = response.data ?? const <String, dynamic>{};
    final defaultMap = body['default'];
    final defaults = defaultMap is Map
        ? defaultMap.map(
            (key, value) => MapEntry(key.toString(), value.toString()),
          )
        : <String, String>{};
    final rawProviders = body['providers'];
    final models = <OpencodeModel>[];
    if (rawProviders is List) {
      for (final item in rawProviders) {
        if (item is! Map) continue;
        final providerId = item['id'] as String?;
        final providerName = item['name'] as String? ?? providerId ?? '';
        if (providerId == null) continue;
        final defaultModelId = defaults[providerId];
        final rawModels = item['models'];
        if (rawModels is Map) {
          final keys = rawModels.keys;
          for (final key in keys) {
            final modelRaw = rawModels[key];
            final modelId = modelRaw is Map
                ? (modelRaw['id'] as String? ?? key)
                : key.toString();
            final modelName = modelRaw is Map
                ? (modelRaw['name'] as String? ?? key)
                : key.toString();
            models.add(
              OpencodeModel(
                providerId: providerId,
                modelId: modelId,
                name: modelName,
                providerName: providerName,
                isDefault: modelId == defaultModelId,
              ),
            );
          }
        }
      }
    }
    return models;
  }

  OpencodeSessionData _parseSession(Map item) {
    final createdMs = _asInt((item['time'] as Map?)?['created']);
    final updatedMs = _asInt((item['time'] as Map?)?['updated']);
    return OpencodeSessionData(
      id: item['id'] as String? ?? '',
      title: item['title'] as String? ?? '未命名会话',
      directory: item['directory'] as String? ?? '',
      parentId: item['parentID'] as String?,
      agent: item['agent'] as String?,
      createdAt: createdMs == null
          ? null
          : DateTime.fromMillisecondsSinceEpoch(createdMs),
      updatedAt: updatedMs == null
          ? null
          : DateTime.fromMillisecondsSinceEpoch(updatedMs),
    );
  }

  int? _asInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return null;
  }

  /// 把 file part 里的引用路径归一为本地文件路径（支持 file:// URI、\\ 分隔）。
  static String _normalizePath(String raw) {
    if (raw.startsWith('file://')) {
      try {
        return Uri.parse(raw).toFilePath();
      } catch (_) {}
    }
    if (raw.startsWith('file:')) {
      return raw.substring('file:'.length);
    }
    return raw.replaceAll('/', '\\');
  }
}
