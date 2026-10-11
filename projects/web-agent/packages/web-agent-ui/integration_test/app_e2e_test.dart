import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:web_agent_ui/controller/app_controller.dart';
import 'package:web_agent_ui/controller/opencode_controller.dart';
import 'package:web_agent_ui/services/daemon_client.dart';
import 'package:web_agent_ui/services/isar_service.dart';
import 'package:web_agent_ui/services/opencode_client.dart';
import 'package:web_agent_ui/ui/home_shell.dart';
import 'package:web_agent_ui/ui/opencode/opencode_view.dart';

import 'support/mock_daemon_server.dart';
import 'support/mock_opencode_server.dart';

/// 真实窗口 E2E：注入两个 mock 后端（随机端口），验证关键交互链路。
/// 不触真实 4096 / 19305，不污染真实 isar 数据。
void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  // fadePointers（默认）只在 tester.pump/指针活动时泵帧，框架
  // scheduleFrame（notifyListeners/setState）会被忽略且 pump 可能永久挂起；
  // fullyLive 与真实应用一致：框架请求的帧都会被渲染。
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  const tempDir = 'build/e2e_tmp';
  final mdFile = File('$tempDir/generated.md');
  final docxFile = File('$tempDir/generated.docx');

  late MockOpencodeServer opencodeServer;
  late MockDaemonServer daemonServer;
  late IsarService isarService;
  late OpencodeClient opencodeClient;
  late DaemonClient daemonClient;
  late AppController webAgentController;
  late OpencodeController opencodeController;

  // 会话 ids（启动前种子化）
  const rootId = 'sess-root';
  const childId = 'sess-child';

  // live binding 下 tester.pump 会等待帧完成，偶发永久挂起（帧管线停摆时）；
  // 真实窗口应用自行渲染帧，测试侧只需用真实时间等待 + 同步读取 widget 树。
  Future<void> waitFor(
    WidgetTester tester,
    Finder finder, {
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
      if (finder.evaluate().isNotEmpty) return;
    }
    fail('等超时: $finder 未出现');
  }

  Future<void> waitForTrue(
    WidgetTester tester,
    bool Function() condition, {
    required String reason,
    Duration timeout = const Duration(seconds: 15),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
      if (condition()) return;
    }
    fail('等超时: $reason');
  }

  Future<void> pause(WidgetTester tester, int ms) async {
    await Future<void>.delayed(Duration(milliseconds: ms));
  }

  /// 消息列表是 lazy 构建的 ListView.builder：末尾消息在视口外时不会进树。
  /// 滚到底部使其构建（消息列表是唯一 SliverChildBuilderDelegate 的 ListView）。
  Future<void> scrollMessagesToBottom(WidgetTester tester) async {
    final messageList = find.byWidgetPredicate(
      (w) => w is SliverList && w.delegate is SliverChildBuilderDelegate,
    );
    final scrollable = find
        .ancestor(of: messageList, matching: find.byType(Scrollable))
        .first;
    final position = tester.state<ScrollableState>(scrollable).position;
    position.jumpTo(position.maxScrollExtent);
    await pause(tester, 300);
  }

  String inputText(WidgetTester tester) => tester
      .widget<TextField>(find.byKey(const ValueKey('message-input')))
      .controller!
      .text;

  /// 点击发送按钮（live binding 下偶发 tap 落空/enterText 不粘：先点击聚焦，
  /// 校验文本写入成功，未写入即重试）。
  Future<void> sendViaButton(WidgetTester tester, String text) async {
    for (var attempt = 0; attempt < 3; attempt++) {
      await pause(tester, 200);
      await tester.tap(
        find.byKey(const ValueKey('message-input')),
        warnIfMissed: false,
      );
      await pause(tester, 120);
      await tester.enterText(
        find.byKey(const ValueKey('message-input')),
        text,
      );
      await pause(tester, 150);
      final got = inputText(tester).trim();
      debugPrint(
        '[e2e] sendViaButton 尝试${attempt + 1} enterText后 输入框="$got" 目标="$text"',
      );
      if (got == text.trim()) break;
      if (attempt == 2) {
        fail('enterText 连续 3 次未写入目标文本：实际 "$got"');
      }
    }
    for (var i = 0; i < 3; i++) {
      await tester.tap(
        find.byKey(const ValueKey('send-button')),
        warnIfMissed: false,
      );
      await pause(tester, 400);
      if (inputText(tester).isEmpty) return; // _send 已接管输入并清空
      debugPrint('[e2e] send 第 ${i + 1} 次 tap 后输入框未清空，重试');
    }
    fail('send-button 连续 3 次 tap 输入框仍未清空，tap 未生效');
  }

  Future<void> settleBusy(WidgetTester tester, String sessionId) async {
    opencodeServer.idle(sessionId);
    await waitFor(
      tester,
      find.byKey(const ValueKey('send-button')),
      timeout: const Duration(seconds: 8),
    );
  }

  testWidgets('E2E：web-agent 桌面端核心链路（真实窗口 + mock 后端）',
      (tester) async {
    final temp = Directory(tempDir);
    if (temp.existsSync()) temp.deleteSync(recursive: true);
    temp.createSync(recursive: true);
    mdFile.writeAsStringSync('生成的 Markdown 内容。\n引用了关键设计。');
    final archive = Archive()
      ..add(
        ArchiveFile.bytes(
          'word/document.xml',
          utf8.encode(
            '<w:document><w:body>'
            '<w:p><w:r><w:t>文档正文第一段。</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>第二段内容。</w:t></w:r></w:p>'
            '</w:body></w:document>',
          ),
        ),
      );
    docxFile.writeAsBytesSync(ZipEncoder().encode(archive));

    opencodeServer = MockOpencodeServer(autoReply: false);
    daemonServer = MockDaemonServer();
    final opencodePort = await opencodeServer.start();
    final daemonPort = await daemonServer.start();

    opencodeServer.seedSession(rootId, title: '根会话');
    opencodeServer.seedSession(childId, title: '子会话', parentId: rootId);

    isarService = IsarService(directory: tempDir);
    opencodeClient = OpencodeClient(baseUrl: 'http://127.0.0.1:$opencodePort');
    daemonClient = await DaemonClient.create(port: daemonPort);

    webAgentController = AppController(
      isarService: isarService,
      daemonClient: daemonClient,
    );
    await webAgentController.init();
    opencodeController = OpencodeController(client: opencodeClient);
    await opencodeController.init();

    await tester.pumpWidget(
      MaterialApp(
        title: 'opencode · Web Agent',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorSchemeSeed: const Color(0xFF4F46E5),
          useMaterial3: true,
        ),
        home: HomeShell(
          webAgentController: webAgentController,
          opencodeController: opencodeController,
        ),
      ),
    );
    await waitFor(tester, find.byKey(const ValueKey('message-input')));
    // opencode 会话已就绪并选中根会话
    expect(opencodeController.activeSessionId, rootId);
    expect(opencodeController.serverReachable, isTrue);

    // ---------------------------------------------------------------
    // 1) 扩展提醒翻转（web-agent 模式 banner）+ Shift+End 回 opencode
    // ---------------------------------------------------------------
    debugPrint('[e2e-1] daemon 状态翻转');
    await tester.tap(find.byType(Switch));
    await pause(tester, 300);
    expect(find.text('Web-Agent 模式'), findsWidgets);
    expect(find.textContaining('已连接 daemon'), findsOneWidget);

    daemonServer.extensionConnected = false;
    await webAgentController.refreshDaemonStatus();
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.textContaining('扩展未连接'), findsOneWidget);

    daemonServer.extensionConnected = true;
    await webAgentController.refreshDaemonStatus();
    await tester.pump(const Duration(milliseconds: 200));
    expect(find.textContaining('已连接 daemon'), findsOneWidget);

    // 切回 opencode 模式（快捷键配置在 test/home_shell_shortcuts_test.dart 单测锁定）
    await tester.tap(find.byType(Switch));
    await pause(tester, 300);
    expect(find.text('opencode'), findsWidgets);
    expect(find.byKey(const ValueKey('message-input')), findsOneWidget);

// ---------------------------------------------------------------
    // 2) 停止生成（点击 stop-button；End 快捷键为单测覆盖）
    // ---------------------------------------------------------------
    debugPrint('[e2e-2] 停止生成');
    await sendViaButton(tester, '慢速任务');
    await waitFor(tester, find.byKey(const ValueKey('stop-button')));
    await pause(tester, 500);
    await tester.tap(find.byKey(const ValueKey('stop-button')));
    await waitFor(tester, find.byKey(const ValueKey('send-button')));
    await waitForTrue(
      tester,
      () => opencodeServer.abortCalls.contains(rootId),
      reason: 'stop 应触发 abort 调用',
    );

    // ---------------------------------------------------------------
    // 3) 模型切换
    // ---------------------------------------------------------------
    debugPrint('[e2e-3] 模型切换');
    await tester.tap(find.byKey(const ValueKey('model-dropdown')));
    await waitFor(tester, find.text('DeepSeek/deepseek-r1'));
    await tester.tap(find.text('DeepSeek/deepseek-r1').last);
    await pause(tester, 400);
    await waitForTrue(
      tester,
      () => opencodeController.selectedModelId == 'deepseek/deepseek-r1',
      reason: '选中 deepseek/deepseek-r1 应反映到 controller',
    );
    await pause(tester, 600);
    expect(find.text('DeepSeek/deepseek-r1'), findsWidgets);

    // ---------------------------------------------------------------
    // 4) 附件：注入附件→发送带 file part→模型回复→附件栏清空
    // ---------------------------------------------------------------
    debugPrint('[e2e-4] 附件链路');
    opencodeController.attachments = const [
      OpencodeAttachment(
        path: 'C:/tmp/计划.md',
        name: '计划.md',
        mime: 'text/markdown',
      ),
    ];
    opencodeController.notifyListeners();
    await pause(tester, 1000);
    final viewCtrl = tester
        .widget<OpencodeView>(find.byType(OpencodeView).first)
        .controller;
    debugPrint(
      '[e2e-4] 注入后 attachments=${opencodeController.attachments.length} '
      '实例一致=${identical(opencodeController, viewCtrl)} '
      'InputChip数=${find.byType(InputChip).evaluate().length} '
      'chip文本数=${find.text('计划.md').evaluate().length} '
      'messageInput在=${find.byKey(const ValueKey('message-input')).evaluate().length}',
    );
    await waitFor(tester, find.text('计划.md'),
        timeout: const Duration(seconds: 8));

    final sendBtn = tester.widget<FilledButton>(
      find.byKey(const ValueKey('send-button')),
    );
    debugPrint(
      '[e2e-4] send 前 serverReachable=${opencodeController.serverReachable} '
      'sending=${opencodeController.sendingOwn} '
      '模型=${opencodeController.selectedModelId} '
      '按钮可用=${sendBtn.onPressed != null} '
      'promptCalls=${opencodeServer.promptAsyncCalls.length}',
    );
    await sendViaButton(tester, '分析这个附件');
    await pause(tester, 1500);
    debugPrint(
      '[e2e-4] send 后 promptCalls=${opencodeServer.promptAsyncCalls.length} '
      'busy=${opencodeController.sending} '
      'ownPending=${opencodeController.sendingOwn} '
      'externalBusy=${opencodeController.externalBusyForActive} '
      'lastError=${opencodeController.lastError} '
      '输入框="${inputText(tester)}" '
      '接管框=${find.text('接管').evaluate().isNotEmpty} '
      '失败提示=${find.textContaining('发送失败').evaluate().isNotEmpty} '
      'send按钮在=${find.byKey(const ValueKey('send-button')).evaluate().isNotEmpty}',
    );
    await waitFor(tester, find.byKey(const ValueKey('stop-button')));
    // 等模型回复出现后再继续下一步
    opencodeServer.insertMessage(
      rootId,
      role: 'assistant',
      text: '附件已分析完毕。',
    );
    opencodeServer.idle(rootId);
    await waitFor(tester, find.text('附件已分析完毕。'));
    await waitFor(tester, find.byKey(const ValueKey('send-button')));

    final promptParts =
        (opencodeServer.promptAsyncCalls.last['parts'] as List)
            .whereType<Map>()
            .toList();
    expect(
      promptParts.any((p) => p['type'] == 'file' && p['filename'] == '计划.md'),
      isTrue,
      reason: '发送应携带 file part',
    );
    expect(opencodeController.attachments, isEmpty,
        reason: '发送后附件栏应清空');

    // ---------------------------------------------------------------
    // 5) 终端 ↔ UI 双向同步 + 外部忙态接管
    // ---------------------------------------------------------------
    debugPrint('[e2e-5] 双向同步与接管');
    await sendViaButton(tester, 'UI 发的消息');
    await waitFor(tester, find.byKey(const ValueKey('stop-button')));
    final uiParts = (opencodeServer.promptAsyncCalls.last['parts'] as List)
        .whereType<Map>()
        .toList();
    expect(uiParts.first['text'], 'UI 发的消息');
    opencodeServer.insertMessage(rootId, role: 'assistant', text: 'UI 消息的模型回复');
    opencodeServer.idle(rootId);
    await waitFor(tester, find.text('UI 消息的模型回复'));
    await waitFor(tester, find.byKey(const ValueKey('send-button')));
    await waitFor(tester, find.text('UI 发的消息'));

    // 终端侧驱动一轮（busy→消息→idle）
    await opencodeServer.terminalRun(rootId, '终端问的问题', reply: '终端答复内容');
    await pause(tester, 800);
    debugPrint(
      '[e2e-5] terminalRun后 messages='
      '${opencodeController.messages.map((m) => '${m.role}: ${m.content}')}',
    );
    await waitFor(tester, find.text('终端答复内容'));
    await waitFor(tester, find.byKey(const ValueKey('send-button')));
    expect(find.text('终端问的问题'), findsOneWidget);

    // 外部忙态：出现琥珀横幅，发送弹接管框
    opencodeServer.setStatus(rootId, 'busy');
    await waitFor(tester, find.byKey(const ValueKey('external-busy-banner')));
    await pause(tester, 300);
    await sendViaButton(tester, '要接管的操作');
    await waitFor(tester, find.text('接管'));
    expect(find.text('取消'), findsOneWidget);

    // 先取消：不应发出 prompt，输入恢复
    await tester.tap(find.text('取消'));
    await pause(tester, 300);
    final callsBeforeCancel = opencodeServer.promptAsyncCalls.length;
    await pause(tester, 200);
    expect(opencodeServer.promptAsyncCalls.length, callsBeforeCancel);
    expect(
      tester.widget<TextField>(find.byKey(const ValueKey('message-input'))).controller!.text,
      '要接管的操作',
    );

    // 再发送 → 接管确认 → abort + prompt
    await tester.tap(find.byKey(const ValueKey('send-button')));
    await waitFor(tester, find.text('接管'));
    await tester.tap(find.text('接管'));
    await waitFor(tester, find.byKey(const ValueKey('stop-button')));
    // stop-button 由 _setBusy 的 notifyListeners 触发，早于 sendPromptAsync 的
    // HTTP 到达 mock；直接断言会竞态（Expected 4 Actual 3），改为等 prompt 落地
    await waitForTrue(
      tester,
      () => opencodeServer.promptAsyncCalls.length >= callsBeforeCancel + 1,
      reason: '接管后应发起一次 prompt_async',
    );
    await settleBusy(tester, rootId);

    // ---------------------------------------------------------------
    // 6) /compact：压缩会话
    // ---------------------------------------------------------------
    debugPrint('[e2e-6] /compact');
    await pause(tester, 300);
    await tester.tap(find.byKey(const ValueKey('message-input')));
    await tester.enterText(find.byKey(const ValueKey('message-input')), '/compact');
    await pause(tester, 200);
    await tester.tap(find.byKey(const ValueKey('send-button')));
    await waitForTrue(
      tester,
      () => opencodeServer.summarizeCalls.contains(rootId),
      reason: '/compact 应触发 summarize 调用',
    );
    await pause(tester, 600);

    // ---------------------------------------------------------------
    // 7) 删除提醒：取消时不真删
    // ---------------------------------------------------------------
    debugPrint('[e2e-7] 删除确认');
    opencodeServer.setStatus(rootId, 'busy');
    await waitFor(tester, find.byKey(const ValueKey('external-busy-banner')));
    final sessionsBefore = opencodeServer.sessionList.length;
    await tester.tap(find.byKey(ValueKey('delete-session-$rootId')));
    await waitFor(tester, find.byKey(const ValueKey('delete-cancel')));
    expect(find.textContaining('子会话'), findsOneWidget);
    expect(find.textContaining('opencode 终端'), findsWidgets);
    await tester.tap(find.byKey(const ValueKey('delete-cancel')));
    await pause(tester, 300);
    expect(opencodeServer.sessionList.length, sessionsBefore);
    expect(opencodeServer.deletedSessions, isEmpty);
    opencodeServer.idle(rootId);
    await tester.pump(const Duration(milliseconds: 200));

    // ---------------------------------------------------------------
    // 8) mermaid 架构图渲染
    // ---------------------------------------------------------------
    debugPrint('[e2e-8] mermaid 渲染');
    // 生成期间会话 busy（模拟真实耗时），兜底轮询据此拉到消息
    await opencodeServer.assistantRun(
      rootId,
      text: '架构图如下：\n\n```mermaid\ngraph TD\nA[前端]\nB[后端]\nA --> B\n```\n\n以上是整体设计。',
    );
    await waitForTrue(
      tester,
      () => opencodeController.messages.length >= 9,
      reason: 'mermaid 消息应进入 controller',
    );
    await scrollMessagesToBottom(tester);
    await waitFor(tester, find.byKey(const ValueKey('mermaid-diagram')));
    expect(find.textContaining('整体设计'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('mermaid-diagram')));
    await waitFor(tester, find.text('架构图'));
    expect(find.text('关闭'), findsOneWidget);
    await tester.tap(find.text('关闭'));
    await pause(tester, 300);

    // ---------------------------------------------------------------
    // 9) 生成文件列表 + 预览（md 文本 / docx 提取）
    // ---------------------------------------------------------------
    debugPrint('[e2e-9] 文件预览');
    // 生成文件期间会话 busy（模拟真实耗时），兜底轮询据此拉到消息
    await opencodeServer.assistantRun(
      rootId,
      text: '为你生成以下文件：',
      fileParts: [
        {'type': 'file', 'path': mdFile.path, 'mime': 'text/markdown'},
        {
          'type': 'file',
          'path': docxFile.path,
          'mime': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        },
      ],
    );
    await waitForTrue(
      tester,
      () => opencodeController.messages.length >= 10,
      reason: '文件消息应进入 controller',
    );
    await scrollMessagesToBottom(tester);
    await waitFor(tester, find.text('generated.md'));
    expect(find.text('generated.docx'), findsOneWidget);

    await tester.tap(find.text('generated.md'));
    // 预览把整个文件内容渲染成单个 SelectableText，用包含匹配
    await waitFor(tester, find.textContaining('引用了关键设计。'));
    expect(find.text('用系统打开'), findsOneWidget);
    await tester.tap(find.text('关闭'));
    await pause(tester, 300);

    await tester.tap(find.text('generated.docx'));
    await waitFor(tester, find.textContaining('文档正文第一段。'));
    expect(find.textContaining('第二段内容。'), findsWidgets);
    await tester.tap(find.text('关闭'));
    await pause(tester, 300);

    debugPrint('[e2e] 全部链路验证通过');
  }, timeout: const Timeout(Duration(minutes: 5)));

  tearDownAll(() async {
    opencodeController.dispose();
    await isarService.isar.close();
    await opencodeServer.stop();
    await daemonServer.stop();
    final temp = Directory(tempDir);
    if (temp.existsSync()) temp.deleteSync(recursive: true);
    debugPrint('[e2e] mock 后端已关闭，临时目录已清理');
  });
}