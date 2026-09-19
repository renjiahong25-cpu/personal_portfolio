import 'dart:io';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:web_agent_ui/controller/app_controller.dart';
import 'package:web_agent_ui/controller/opencode_controller.dart';
import 'package:web_agent_ui/services/daemon_client.dart';
import 'package:web_agent_ui/services/isar_service.dart';
import 'package:web_agent_ui/services/opencode_client.dart';
import 'package:web_agent_ui/ui/home_shell.dart';

import 'support/mock_daemon_server.dart';
import 'support/mock_opencode_server.dart';

/// 临时截图测试：mock 后端驱动真实窗口（真实字体渲染），
/// 切到 Web-Agent 模式、发一条双模型消息，导出 PNG 到仓库 docs/assets。
void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  const tempDir = 'build/e2e_tmp';
  const outPath = r'D:\Program Files\web-agent\docs\assets\web-agent-ui-screenshot.png';

  late MockOpencodeServer opencodeServer;
  late MockDaemonServer daemonServer;
  late IsarService isarService;
  late AppController webAgentController;
  late OpencodeController opencodeController;

  Future<void> pause(WidgetTester tester, int ms) async {
    await Future<void>.delayed(Duration(milliseconds: ms));
  }

  Future<void> waitForTrue(
    WidgetTester tester,
    bool Function() condition, {
    required String reason,
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final deadline = DateTime.now().add(timeout);
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 200));
      if (condition()) return;
    }
    fail('等超时: $reason');
  }

  testWidgets('screenshot web-agent mode', (tester) async {
    final temp = Directory(tempDir);
    if (temp.existsSync()) temp.deleteSync(recursive: true);
    temp.createSync(recursive: true);

    opencodeServer = MockOpencodeServer(autoReply: false);
    daemonServer = MockDaemonServer();
    daemonServer.replyContentBySite = {
      'deepseek':
          '先说价值：桌面客户端让用户不必整天开着浏览器，任务状态本地持久化，方便长期编排；其次统一了群聊与 opencode 的入口，上下文切换成本低；最后为本地自动化留了口子（拉起 daemon、挂扩展、监控任务）。',
      'doubao':
          '从产品角度：1) 专属窗口的打扰成本比浏览器标签更低；2) 多模型并行回复便于并排对比；3) 更贴合 local-first 的隐私预期，数据只落在用户本机。',
    };
    final opencodePort = await opencodeServer.start();
    final daemonPort = await daemonServer.start();
    opencodeServer.seedSession('sess-1', title: '演示会话');

    isarService = IsarService(directory: tempDir);
    final daemonClient = await DaemonClient.create(port: daemonPort);
    webAgentController = AppController(
      isarService: isarService,
      daemonClient: daemonClient,
    );
    await webAgentController.init();
    opencodeController = OpencodeController(
      client: OpencodeClient(baseUrl: 'http://127.0.0.1:$opencodePort'),
    );
    await opencodeController.init();

    await tester.pumpWidget(
      MaterialApp(
        title: 'opencode · Web Agent',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          colorSchemeSeed: const Color(0xFF4F46E5),
          useMaterial3: true,
        ),
        home: RepaintBoundary(
          key: const ValueKey('shot'),
          child: HomeShell(
            webAgentController: webAgentController,
            opencodeController: opencodeController,
          ),
        ),
      ),
    );

    await waitForTrue(
      tester,
      () => find.byKey(const ValueKey('message-input')).evaluate().isNotEmpty,
      reason: 'opencode 模式输入框应出现',
      timeout: const Duration(seconds: 30),
    );
    await pause(tester, 500);

    // 切到 Web-Agent 模式
    await tester.tap(find.byType(Switch));
    await waitForTrue(
      tester,
      () => find.textContaining('已连接 daemon').evaluate().isNotEmpty,
      reason: 'web-agent 模式应显示 daemon 状态',
    );
    await pause(tester, 600);

    // 窗口标题
    final window = webAgentController.windows.first;
    window.title = '方案评审';
    webAgentController.notifyListeners();
    await pause(tester, 500);

    // 直接走 controller 发一条双模型消息（mock 立即返回两条 done 回复）
    await webAgentController.sendMessage(
      window.id,
      '请评审一下“把群聊工作台做成桌面客户端”这个方案，各自列出三个关键点。',
      ['deepseek', 'doubao'],
    );
    await waitForTrue(
      tester,
      () => find.textContaining('先说价值').evaluate().isNotEmpty &&
          find.textContaining('从产品角度').evaluate().isNotEmpty,
      reason: '两条模型回复应渲染出来',
      timeout: const Duration(seconds: 30),
    );
    await pause(tester, 1000);

    // 导出 PNG
    final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('shot')),
    );
    final ratio = tester.view.devicePixelRatio > 2.0
        ? 2.0
        : tester.view.devicePixelRatio;
    final image = await boundary.toImage(pixelRatio: ratio);
    final data = await image.toByteData(format: ImageByteFormat.png);
    expect(data, isNotNull);
    File(outPath).writeAsBytesSync(data!.buffer.asUint8List());
    debugPrint('[shot] saved: $outPath (${data.lengthInBytes} bytes)');
  }, timeout: const Timeout(Duration(minutes: 5)));

  tearDownAll(() async {
    opencodeController.dispose();
    await isarService.isar.close();
    await opencodeServer.stop();
    await daemonServer.stop();
    final temp = Directory(tempDir);
    if (temp.existsSync()) temp.deleteSync(recursive: true);
    debugPrint('[shot] mock 后端已关闭，临时目录已清理');
  });
}
