import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:web_agent_ui/ui/home_shell.dart';

void main() {
  test('HomeShell 快捷键契约：Shift+End 切 opencode，End 中止任务', () {
    expect(HomeShell.shortcutMap.length, 2);
    expect(
      HomeShell.shortcutMap,
      containsPair(
        const SingleActivator(LogicalKeyboardKey.end, shift: true),
        isA<SwitchToOpencodeIntent>(),
      ),
    );
    expect(
      HomeShell.shortcutMap,
      containsPair(
        const SingleActivator(LogicalKeyboardKey.end),
        isA<StopActiveTaskIntent>(),
      ),
    );
  });
}