import 'package:flutter/material.dart';

import '../controller/app_controller.dart';
import 'chat_window_view.dart';

class WindowsShell extends StatelessWidget {
  const WindowsShell({super.key, required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(width: 220, child: _WindowSidebar(controller: controller)),
          const VerticalDivider(width: 1),
          Expanded(
            child: _activeView(controller),
          ),
        ],
      ),
    );
  }

  Widget _activeView(AppController controller) {
    final active = controller.activeWindow;
    if (active == null) {
      return const Center(child: Text('没有打开的窗口'));
    }
    return ChatWindowView(
      key: ValueKey(active.id),
      window: active,
      controller: controller,
    );
  }
}

class _WindowSidebar extends StatelessWidget {
  const _WindowSidebar({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Text('窗口', style: theme.textTheme.titleSmall),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            children: [
              for (final window in controller.windowList)
                _WindowTile(
                  title: window.title,
                  selected: window.id == controller.activeWindowId,
                  onTap: () => controller.selectWindow(window.id),
                  onClose: () => controller.closeWindow(window.id),
                ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(8),
          child: FilledButton.tonalIcon(
            onPressed: () => controller.createWindow(),
            icon: const Icon(Icons.add),
            label: const Text('新建窗口'),
          ),
        ),
      ],
    );
  }
}

class _WindowTile extends StatelessWidget {
  const _WindowTile({
    required this.title,
    required this.selected,
    required this.onTap,
    required this.onClose,
  });

  final String title;
  final bool selected;
  final VoidCallback onTap;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Material(
      color: selected ? theme.colorScheme.secondaryContainer : null,
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium,
                ),
              ),
              IconButton(
                onPressed: onClose,
                icon: const Icon(Icons.close, size: 16),
                visualDensity: VisualDensity.compact,
                tooltip: '关闭窗口',
              ),
            ],
          ),
        ),
      ),
    );
  }
}