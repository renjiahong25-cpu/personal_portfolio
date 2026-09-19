import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controller/app_controller.dart';
import '../controller/opencode_controller.dart';
import 'opencode/opencode_view.dart';
import 'windows_shell.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.webAgentController,
    required this.opencodeController,
  });

  final AppController webAgentController;
  final OpencodeController opencodeController;

  /// 全局快捷键映射：Shift+End 切回 opencode 模式；End 中止当前任务。
  static const Map<ShortcutActivator, Intent> shortcutMap = {
    SingleActivator(LogicalKeyboardKey.end, shift: true):
        SwitchToOpencodeIntent(),
    SingleActivator(LogicalKeyboardKey.end): StopActiveTaskIntent(),
  };

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  bool _webAgentMode = false;

  void _switchToOpencode() {
    if (_webAgentMode) setState(() => _webAgentMode = false);
  }

  void _stopActiveTask() {
    if (_webAgentMode) {
      final windowId = widget.webAgentController.activeWindowId;
      if (windowId != null) {
        widget.webAgentController.stopTask(windowId);
      }
    } else {
      widget.opencodeController.stop();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Shortcuts(
      shortcuts: HomeShell.shortcutMap,
      child: Actions(
        actions: {
          SwitchToOpencodeIntent: CallbackAction<SwitchToOpencodeIntent>(
            onInvoke: (_) => _switchToOpencode(),
          ),
          StopActiveTaskIntent: CallbackAction<StopActiveTaskIntent>(
            onInvoke: (_) => _stopActiveTask(),
          ),
        },
        child: Scaffold(
          appBar: AppBar(
            title: Row(
              children: [
                const Icon(Icons.memory, size: 20),
                const SizedBox(width: 8),
                Text(_webAgentMode ? 'Web-Agent 模式' : 'opencode'),
              ],
            ),
            actions: [
              Padding(
                padding: const EdgeInsets.only(right: 12),
                child: Row(
                  children: [
                    Text(
                      'Web-Agent 模式',
                      style: Theme.of(context).textTheme.labelLarge,
                    ),
                    const SizedBox(width: 8),
                    Switch(
                      value: _webAgentMode,
                      onChanged: (value) => setState(() => _webAgentMode = value),
                    ),
                  ],
                ),
              ),
            ],
          ),
          body: _webAgentMode
              ? AnimatedBuilder(
                  animation: widget.webAgentController,
                  builder: (context, _) =>
                      WindowsShell(controller: widget.webAgentController),
                )
              : OpencodeView(controller: widget.opencodeController),
        ),
      ),
    );
  }
}

class SwitchToOpencodeIntent extends Intent {
  const SwitchToOpencodeIntent();
}

class StopActiveTaskIntent extends Intent {
  const StopActiveTaskIntent();
}
