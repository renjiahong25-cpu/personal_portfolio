import 'dart:async';

import 'package:flutter/material.dart';

import 'controller/app_controller.dart';
import 'controller/opencode_controller.dart';
import 'services/daemon_client.dart';
import 'services/isar_service.dart';
import 'services/opencode_client.dart';
import 'ui/home_shell.dart';

class _BootResult {
  const _BootResult({
    required this.webAgentController,
    required this.opencodeController,
  });

  final AppController webAgentController;
  final OpencodeController opencodeController;
}

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const WebAgentUiApp());
}

class WebAgentUiApp extends StatefulWidget {
  const WebAgentUiApp({
    super.key,
    this.isarService,
    this.daemonClient,
    this.opencodeClient,
  });

  /// 注入点：集成测试可传入 mock 后端与临时 isar 目录，默认走真实环境。
  final IsarService? isarService;
  final DaemonClient? daemonClient;
  final OpencodeClient? opencodeClient;

  @override
  State<WebAgentUiApp> createState() => _WebAgentUiAppState();
}

class _WebAgentUiAppState extends State<WebAgentUiApp> {
  late Future<_BootResult> _future;
  AppController? _webAgentController;
  OpencodeController? _opencodeController;
  Timer? _statusTimer;

  @override
  void initState() {
    super.initState();
    _future = _boot();
  }

  Future<_BootResult> _boot() async {
    final isarService = widget.isarService ?? IsarService();
    final daemonClient =
        widget.daemonClient ?? await DaemonClient.create();
    final webAgentController = AppController(
      isarService: isarService,
      daemonClient: daemonClient,
    );
    await webAgentController.init();
    _webAgentController = webAgentController;

    final opencodeController = OpencodeController(
      client: widget.opencodeClient,
    );
    await opencodeController.init();
    _opencodeController = opencodeController;

    _statusTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      _webAgentController?.refreshDaemonStatus();
    });
    return _BootResult(
      webAgentController: webAgentController,
      opencodeController: opencodeController,
    );
  }

  @override
  void dispose() {
    _statusTimer?.cancel();
    _opencodeController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'opencode · Web Agent',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF4F46E5),
        useMaterial3: true,
      ),
      home: FutureBuilder<_BootResult>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Scaffold(
              body: Center(child: CircularProgressIndicator()),
            );
          }
          if (snapshot.hasError) {
            return Scaffold(
              body: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text('启动失败：${snapshot.error}'),
                ),
              ),
            );
          }
          final result = snapshot.data!;
          return HomeShell(
            webAgentController: result.webAgentController,
            opencodeController: result.opencodeController,
          );
        },
      ),
    );
  }
}