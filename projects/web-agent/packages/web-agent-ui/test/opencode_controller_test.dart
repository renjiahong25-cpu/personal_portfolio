import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:web_agent_ui/controller/opencode_controller.dart';
import 'package:web_agent_ui/services/opencode_client.dart';

/// 可注入的假客户端：只记录调用、不触网。
class FakeOpencodeClient extends OpencodeClient {
  FakeOpencodeClient() : _eventCtrl = StreamController<OpencodeEvent>.broadcast();

  final StreamController<OpencodeEvent> _eventCtrl;

  bool healthy = true;
  String? version = 'test';
  List<OpencodeSessionData> sessions = [];
  List<OpencodeMessage> messages = [];
  List<OpencodeModel> models = [];

  /// /session/status 模拟返回（SSE 之外的兜底状态源）。
  Map<String, String> statuses = {};

  String? lastSendSession;
  List<Map<String, dynamic>> lastParts = [];
  String? lastProviderId;
  String? lastModelId;
  String? lastAgent;
  int createCount = 0;
  final List<String> deletedSessions = [];
  final List<String> abortSessions = [];
  String? lastPermissionReply;
  String? lastPermissionDecision;
  String? lastPermissionMessage;
  String? lastQuestionReplyId;
  List<List<String>>? lastQuestionAnswers;
  String? lastRejectedQuestion;

  @override
  Stream<OpencodeEvent> get events => _eventCtrl.stream;

  void emit(OpencodeEvent event) => _eventCtrl.add(event);

  @override
  Future<OpencodeHealth?> health() async =>
      OpencodeHealth(healthy: healthy, version: version);

  @override
  Future<List<OpencodeModel>> listModels() async => models;

  @override
  Future<List<OpencodeCommand>> listCommands() async => const [];

  @override
  Future<List<OpencodeSessionData>> listSessions() async => sessions;

  @override
  Future<List<OpencodeMessage>> listMessages(String sessionId) async =>
      messages;

  @override
  Future<OpencodeSessionData> createSession({String? title}) async {
    createCount++;
    final session = OpencodeSessionData(
      id: 's$createCount',
      title: '新会话',
      directory: '/',
    );
    sessions = [session, ...sessions];
    return session;
  }

  @override
  Future<bool> deleteSession(String sessionId) async {
    deletedSessions.add(sessionId);
    sessions = sessions.where((s) => s.id != sessionId).toList();
    return true;
  }

  @override
  Future<void> sendPromptAsync(
    String sessionId, {
    required List<Map<String, dynamic>> parts,
    String? providerId,
    String? modelId,
    String? agent,
  }) async {
    lastSendSession = sessionId;
    lastParts = parts;
    lastProviderId = providerId;
    lastModelId = modelId;
    lastAgent = agent;
  }

  @override
  Future<bool> abortSession(String sessionId) async {
    abortSessions.add(sessionId);
    return true;
  }

  @override
  Future<Map<String, String>> getSessionStatuses() async => statuses;

  @override
  Future<bool> replyPermission(
    String requestId, {
    required String reply,
    String? message,
  }) async {
    lastPermissionReply = requestId;
    lastPermissionDecision = reply;
    lastPermissionMessage = message;
    return true;
  }

  @override
  Future<bool> replyQuestion(
    String requestId,
    List<List<String>> answers,
  ) async {
    lastQuestionReplyId = requestId;
    lastQuestionAnswers = answers;
    return true;
  }

  @override
  Future<bool> rejectQuestion(String requestId) async {
    lastRejectedQuestion = requestId;
    return true;
  }

  @override
  void dispose() {
    _eventCtrl.close();
  }
}

OpencodeSessionData _session(String id, {String? parentId, int updatedMs = 0}) {
  return OpencodeSessionData(
    id: id,
    title: id,
    directory: '/',
    parentId: parentId,
    createdAt: DateTime.fromMillisecondsSinceEpoch(updatedMs),
    updatedAt: DateTime.fromMillisecondsSinceEpoch(updatedMs),
  );
}

OpencodeMessage _message(String id, String role, String content) {
  return OpencodeMessage(
    id: id,
    role: role,
    content: content,
    createdAt: DateTime.fromMillisecondsSinceEpoch(0),
  );
}

OpencodeEvent _statusEvent(String sessionId, String status) {
  return OpencodeEvent(
    id: 'e',
    type: 'session.status',
    properties: {'sessionID': sessionId, 'status': status},
  );
}

OpencodeEvent _idleEvent(String sessionId) {
  return OpencodeEvent(
    id: 'e',
    type: 'session.idle',
    properties: {'sessionID': sessionId},
  );
}

OpencodeEvent _permissionEvent({
  required String id,
  required String sessionId,
  required String permission,
  List<String> patterns = const [],
}) {
  return OpencodeEvent(
    id: 'e',
    type: 'permission.asked',
    properties: {
      'id': id,
      'sessionID': sessionId,
      'permission': permission,
      'patterns': patterns,
    },
  );
}

OpencodeEvent _questionEvent({
  required String id,
  required String sessionId,
  required List<Map<String, dynamic>> questions,
}) {
  return OpencodeEvent(
    id: 'e',
    type: 'question.asked',
    properties: {'id': id, 'sessionID': sessionId, 'questions': questions},
  );
}

Future<void> _flush([int milliseconds = 25]) =>
    Future<void>.delayed(Duration(milliseconds: milliseconds));

void main() {
  group('OpencodeController 发送', () {
    test('无活动会话时先创建会话再发送', () async {
      final fake = FakeOpencodeClient();
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();
      expect(controller.activeSessionId, isNull);

      final ok = await controller.sendMessage('你好');
      expect(ok, isTrue);
      expect(fake.createCount, 1);
      expect(fake.lastSendSession, 's1');
      expect(fake.lastParts.first['text'], '你好');
      expect(controller.sending, isTrue);

      fake.emit(_idleEvent('s1'));
      await _flush();
      expect(controller.sending, isFalse);
    });

    test('发送携带所选模型的 provider/model 与 agent 覆盖', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();
      controller.selectedModelId = 'foo/bar';
      controller.setAgentMode('plan');

      final ok = await controller.sendMessage('x');
      expect(ok, isTrue);
      expect(fake.lastProviderId, 'foo');
      expect(fake.lastModelId, 'bar');
      expect(fake.lastAgent, 'plan');

      fake.emit(_idleEvent('s1'));
      await _flush();
    });

    test('附件以 file part 追加并随发送成功清空', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      controller.addAttachment('C:/tmp/demo.md');
      expect(controller.attachments.length, 1);
      expect(controller.attachments.first.mime, 'text/plain');
      controller.addAttachment('C:/tmp/demo.md');
      expect(controller.attachments.length, 1);
      expect(controller.lastError, contains('文件已添加'));

      final ok = await controller.sendMessage('go');
      expect(ok, isTrue);
      expect(fake.lastParts.length, 2);
      final filePart = fake.lastParts[1];
      expect(filePart['type'], 'file');
      expect(filePart['mime'], 'text/plain');
      expect(filePart['filename'], 'demo.md');
      expect(filePart['url'], startsWith('file://'));
      expect(controller.attachments, isEmpty);

      fake.emit(_idleEvent('s1'));
      await _flush();
    });

    test('外部占用的会话无 takeover 时抛 ExternalBusyException，不 abort 不发送', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_statusEvent('s1', 'busy'));
      await _flush();
      expect(controller.sending, isTrue);
      expect(controller.sendingOwn, isFalse);
      expect(controller.externalBusyForActive, isTrue);

      await expectLater(
        controller.sendMessage('接管'),
        throwsA(isA<ExternalBusyException>()),
      );
      expect(fake.abortSessions, isEmpty);
      expect(fake.lastSendSession, isNull);

      fake.emit(_idleEvent('s1'));
      await _flush();
    });

    test('外部占用的会话 takeover:true 时先 abort 再发送', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_statusEvent('s1', 'busy'));
      await _flush();

      final ok = await controller.sendMessage('接管', takeover: true);
      expect(ok, isTrue);
      expect(fake.abortSessions, contains('s1'));
      expect(fake.lastSendSession, 's1');
      expect(controller.sendingOwn, isTrue);

      fake.emit(_idleEvent('s1'));
      await _flush();
      expect(controller.externalBusyForActive, isFalse);
    });

    test('外部 busy 结束后兜底刷新拉齐最终消息（无需 message.updated）', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();
      fake.messages = [_message('m1', 'user', 'hi')];
      await controller.selectSession('s1');

      // 终端触发：busy 由 SSE 到达，但完成事件全程未到（模拟丢包）
      fake.emit(_statusEvent('s1', 'busy'));
      await _flush();
      expect(controller.externalBusyForActive, isTrue);

      // 服务端已产出回复，仅 /session/status 与消息列表变化（无任何 SSE）
      fake.messages = [
        _message('m1', 'user', 'hi'),
        _message('m2', 'assistant', '回复内容'),
      ];
      fake.statuses = {'s1': 'idle'};

      // 等待下一次 1s 兜底轮询命中 idle → 兜底刷新
      await _flush(1600);
      expect(controller.externalBusyForActive, isFalse);
      expect(controller.sendingOwn, isFalse);
      expect(
        controller.messages.any((m) => m.content == '回复内容'),
        isTrue,
      );
    });

    test('sending / sendingOwn / externalBusyForActive 状态区分', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();
      expect(controller.sending, isFalse);
      expect(controller.sendingOwn, isFalse);
      expect(controller.externalBusyForActive, isFalse);

      // 本端发起：sendingOwn 生效，其他 false
      await controller.sendMessage('hi');
      expect(controller.sending, isTrue);
      expect(controller.sendingOwn, isTrue);
      expect(controller.externalBusyForActive, isFalse);
      fake.emit(_idleEvent('s1'));
      await _flush();

      // 外部触发：仅 externalBusyForActive 生效
      fake.emit(_statusEvent('s1', 'busy'));
      await _flush();
      expect(controller.sending, isTrue);
      expect(controller.sendingOwn, isFalse);
      expect(controller.externalBusyForActive, isTrue);
      expect(controller.isSessionExternallyActive('s1'), isTrue);
      fake.emit(_idleEvent('s1'));
      await _flush();
      expect(controller.externalBusyForActive, isFalse);
    });

    test('stop 中止会话并清理忙态', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_statusEvent('s1', 'busy'));
      await _flush();
      expect(controller.sending, isTrue);

      final ok = await controller.stop();
      expect(ok, isTrue);
      expect(fake.abortSessions, contains('s1'));
      expect(controller.sending, isFalse);
    });
  });

  group('OpencodeController 会话管理', () {
    test('删除会话级联删除全部子会话并落到其他根会话', () async {
      final fake = FakeOpencodeClient()
        ..sessions = [
          _session('r', updatedMs: 4000),
          _session('o', updatedMs: 2000),
          _session('c1', parentId: 'r', updatedMs: 3000),
          _session('c1a', parentId: 'c1', updatedMs: 1000),
        ];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();
      expect(controller.activeSessionId, 'r');

      final ok = await controller.deleteSession('r');
      expect(ok, isTrue);
      expect(fake.deletedSessions, ['r', 'c1', 'c1a']);
      expect(controller.activeSessionId, 'o');
    });

    test('删除当前时间更新消息列表', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.messages = [_message('m1', 'user', 'hi')];
      await controller.selectSession('s1');
      expect(controller.messages.length, 1);
      expect(controller.messages.first.content, 'hi');
    });

    test('refresh 回填默认模型与会话选中', () async {
      final fake = FakeOpencodeClient()
        ..sessions = [_session('s1')]
        ..models = const [
          OpencodeModel(
            providerId: 'p',
            modelId: 'm1',
            name: 'm1',
            providerName: 'P',
            isDefault: true,
          ),
        ];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      expect(controller.serverReachable, isTrue);
      expect(controller.selectedModelId, 'p/m1');
      expect(controller.activeSessionId, 's1');
    });
  });

  group('OpencodeController 确认请求', () {
    test('权限请求进队列并用 always 应答', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_permissionEvent(
        id: 'p1',
        sessionId: 's1',
        permission: 'file.read',
        patterns: ['/tmp/*'],
      ));
      await _flush();
      expect(controller.pendingPermission, isNotNull);
      expect(controller.pendingPermission?.id, 'p1');
      expect(controller.pendingPermission?.patterns, contains('/tmp/*'));

      final ok = await controller.replyPermission(
        decision: OpencodePermissionDecision.always,
        message: '可信路径',
      );
      expect(ok, isTrue);
      expect(fake.lastPermissionReply, 'p1');
      expect(fake.lastPermissionDecision, OpencodePermissionDecision.always);
      expect(fake.lastPermissionMessage, '可信路径');
      expect(controller.pendingPermission, isNull);
      await _flush(400);
    });

    test('权限请求可拒绝', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_permissionEvent(id: 'p1', sessionId: 's1', permission: 'http'));
      await _flush();
      final ok = await controller.replyPermission(
        decision: OpencodePermissionDecision.reject,
      );
      expect(ok, isTrue);
      expect(fake.lastPermissionDecision, OpencodePermissionDecision.reject);
      expect(controller.pendingPermission, isNull);
      await _flush(400);
    });

    test('多步骤问题整组提交', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_questionEvent(
        id: 'q1',
        sessionId: 's1',
        questions: [
          {
            'header': 'Q1',
            'question': '技术栈？',
            'multiple': false,
            'custom': false,
            'options': [
              {'label': 'Rust', 'description': null},
              {'label': 'Go', 'description': null},
            ],
          },
          {
            'header': 'Q2',
            'question': '补充？',
            'multiple': true,
            'custom': true,
            'options': [
              {'label': '无', 'description': null},
              {'label': '有防火墙', 'description': null},
            ],
          },
        ],
      ));
      await _flush();
      expect(controller.pendingQuestion, isNotNull);
      expect(controller.pendingQuestion?.questions.length, 2);
      expect(controller.pendingQuestion?.questions.first.multiple, isFalse);
      expect(controller.pendingQuestion?.questions.last.custom, isTrue);

      final ok = await controller.answerQuestion([
        ['Rust'],
        ['有防火墙'],
      ]);
      expect(ok, isTrue);
      expect(fake.lastQuestionReplyId, 'q1');
      expect(fake.lastQuestionAnswers, [
        ['Rust'],
        ['有防火墙'],
      ]);
      expect(controller.pendingQuestion, isNull);
      await _flush(400);
    });

    test('跳过问题组', () async {
      final fake = FakeOpencodeClient()..sessions = [_session('s1')];
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);
      await controller.init();

      fake.emit(_questionEvent(
        id: 'q1',
        sessionId: 's1',
        questions: [
          {
            'header': 'Q1',
            'question': '继续？',
            'options': [
              {'label': '继续', 'description': null},
            ],
          },
        ],
      ));
      await _flush();
      final ok = await controller.skipQuestion();
      expect(ok, isTrue);
      expect(fake.lastRejectedQuestion, 'q1');
      expect(controller.pendingQuestion, isNull);
      await _flush(400);
    });
  });

  group('OpencodeController 模式', () {
    test('Plan/Build 切换与非法值防护', () async {
      final fake = FakeOpencodeClient();
      final controller = OpencodeController(client: fake);
      addTearDown(controller.dispose);

      expect(controller.agentMode, 'build');
      expect(controller.planMode, isFalse);
      controller.toggleAgentMode();
      expect(controller.planMode, isTrue);
      controller.toggleAgentMode();
      expect(controller.planMode, isFalse);
      controller.setAgentMode('nope');
      expect(controller.agentMode, 'build');
      controller.setAgentMode('plan');
      expect(controller.planMode, isTrue);
    });
  });
}