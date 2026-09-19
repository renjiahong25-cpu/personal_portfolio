import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../controller/opencode_controller.dart';
import '../../services/docx_preview.dart';
import '../../services/opencode_client.dart';
import '../widgets/confirm_wizard.dart';
import '../widgets/mermaid_diagram.dart';

class OpencodeView extends StatefulWidget {
  const OpencodeView({super.key, required this.controller});

  final OpencodeController controller;

  @override
  State<OpencodeView> createState() => _OpencodeViewState();
}

class _OpencodeViewState extends State<OpencodeView> {
  OpencodeController get controller => widget.controller;

  String? _toastText;
  bool _toastError = false;
  Timer? _toastTimer;

  void showToast(String text, {bool error = false}) {
    _toastTimer?.cancel();
    setState(() {
      _toastText = text;
      _toastError = error;
    });
    _toastTimer = Timer(const Duration(milliseconds: 2500), () {
      if (mounted) setState(() => _toastText = null);
    });
  }

  @override
  void dispose() {
    _toastTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        return Stack(
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SizedBox(
                  width: 220,
                  child: _SessionSidebar(
                    controller: controller,
                    onToast: showToast,
                  ),
                ),
                const VerticalDivider(width: 1),
                Expanded(
                  child: controller.activeSession == null
                      ? const _EmptyState()
                      : OpencodeChatView(
                          key: ValueKey(controller.activeSessionId),
                          controller: controller,
                          onToast: showToast,
                        ),
                ),
              ],
            ),
            if (_toastText != null)
              Positioned(
                top: 12,
                left: 0,
                right: 0,
                child: IgnorePointer(
                  child: Center(
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 8,
                      ),
                      decoration: BoxDecoration(
                        color: _toastError
                            ? Theme.of(context).colorScheme.error
                            : Theme.of(context).colorScheme.errorContainer,
                        borderRadius: BorderRadius.circular(16),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.15),
                            blurRadius: 8,
                            offset: const Offset(0, 2),
                          ),
                        ],
                      ),
                      child: Text(
                        _toastText!,
                        style: Theme.of(context)
                            .textTheme
                            .labelSmall
                            ?.copyWith(
                              color: _toastError
                                  ? Theme.of(context).colorScheme.onError
                                  : Theme.of(context)
                                      .colorScheme
                                      .onErrorContainer,
                            ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.forum_outlined,
            size: 40,
            color: theme.colorScheme.outline,
          ),
          const SizedBox(height: 12),
          Text('打开或新建一个 opencode 会话', style: theme.textTheme.bodyMedium),
        ],
      ),
    );
  }
}

class _SessionSidebar extends StatelessWidget {
  const _SessionSidebar({
    required this.controller,
    required this.onToast,
  });

  final OpencodeController controller;
  final void Function(String text, {bool error}) onToast;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Row(
            children: [
              Icon(Icons.terminal, size: 16, color: theme.colorScheme.primary),
              const SizedBox(width: 6),
              Text('会话', style: theme.textTheme.titleSmall),
            ],
          ),
        ),
        _ServerBanner(controller: controller),
        Expanded(
          child: controller.sessions.isEmpty
              ? Center(
                  child: Text(
                    controller.loading ? '加载中…' : '暂无会话',
                    style: theme.textTheme.bodySmall,
                  ),
                )
              : ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  children: [
                    if (controller.sessions.length !=
                        controller.rootSessions.length)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(8, 4, 8, 2),
                        child: Text(
                          '共 ${controller.sessions.length} 个会话，显示 ${controller.rootSessions.length} 个顶层会话',
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: theme.colorScheme.outline,
                          ),
                        ),
                      ),
                    for (final session in controller.rootSessions)
                      _SessionTile(
                        sessionId: session.id,
                        title: session.title,
                        subtitle: _formatTime(session.updatedAt),
                        selected: session.id == controller.activeSessionId,
                        onTap: () => controller.selectSession(session.id),
                        onDelete: () =>
                            _deleteSession(context, session.id),
                      ),
                  ],
                ),
        ),
        Padding(
          padding: const EdgeInsets.all(8),
          child: FilledButton.tonalIcon(
            key: const ValueKey('new-session-button'),
            onPressed: () => controller.createSession(),
            icon: const Icon(Icons.add),
            label: const Text('新建会话'),
          ),
        ),
      ],
    );
  }

  String _formatTime(DateTime? time) {
    if (time == null) return '';
    final now = DateTime.now();
    final local = time.toLocal();
    if (local.year == now.year &&
        local.month == now.month &&
        local.day == now.day) {
      return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
    }
    return '${local.month}/${local.day} ${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  /// 计算该会话（含全部后代子会话）将被连带删除的个数。
  int _descendantCount(String sessionId) {
    final descendants = <String>[sessionId];
    var index = 0;
    while (index < descendants.length) {
      var added = 0;
      for (final session in controller.sessions) {
        if (session.parentId == descendants[index] &&
            !descendants.contains(session.id)) {
          descendants.add(session.id);
          added++;
        }
      }
      if (added == 0) index++;
    }
    return descendants.length - 1;
  }

  Future<void> _deleteSession(BuildContext context, String sessionId) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('确认删除会话'),
        content: Builder(
          builder: (contentContext) {
            final count = _descendantCount(sessionId);
            final external =
                controller.isSessionExternallyActive(sessionId);
            return Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (count > 0)
                  Text('该会话下有 $count 个子会话，将一并删除，无法恢复。')
                else
                  const Text('删除后无法恢复，确定要删除该会话吗？'),
                if (external) ...[
                  const SizedBox(height: 8),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        Icons.warning_amber_rounded,
                        size: 18,
                        color: Theme.of(contentContext).colorScheme.error,
                      ),
                      const SizedBox(width: 6),
                      const Expanded(
                        child: Text(
                          '该会话可能正被 opencode 终端使用，删除将同时删除服务端会话。',
                          style: TextStyle(fontSize: 12),
                        ),
                      ),
                    ],
                  ),
                ],
              ],
            );
          },
        ),
        actions: [
          TextButton(
            key: const ValueKey('delete-cancel'),
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('取消'),
          ),
          FilledButton(
            key: const ValueKey('delete-confirm'),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(dialogContext).colorScheme.error,
              foregroundColor: Theme.of(dialogContext).colorScheme.onError,
            ),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final ok = await controller.deleteSession(sessionId);
    if (!context.mounted) return;
    onToast(
      ok
          ? '已删除会话'
          : '删除失败：${controller.lastError ?? '未知错误'}',
      error: !ok,
    );
  }
}

class _ServerBanner extends StatelessWidget {
  const _ServerBanner({required this.controller});

  final OpencodeController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final reachable = controller.serverReachable;
    final color = reachable
        ? theme.colorScheme.primary
        : theme.colorScheme.error;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: Row(
        children: [
          Icon(
            reachable ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
            size: 14,
            color: color,
          ),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              reachable
                  ? 'opencode server 已连接${controller.serverVersion != null ? ' v${controller.serverVersion}' : ''}'
                  : 'opencode server 未运行：请先运行 tools/start-ui.ps1',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(color: color),
            ),
          ),
        ],
      ),
    );
  }
}

class _SessionTile extends StatefulWidget {
  const _SessionTile({
    required this.sessionId,
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
    required this.onDelete,
  });

  final String sessionId;
  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  @override
  State<_SessionTile> createState() => _SessionTileState();
}

class _SessionTileState extends State<_SessionTile> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Material(
        color: widget.selected
            ? theme.colorScheme.secondaryContainer
            : _hovered
                ? theme.colorScheme.surfaceContainerHighest
                : null,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          onTap: widget.onTap,
          borderRadius: BorderRadius.circular(8),
          child: Row(
            children: [
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        widget.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodyMedium,
                      ),
                      if (widget.subtitle.isNotEmpty)
                        Text(
                          widget.subtitle,
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: theme.colorScheme.outline,
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              IconButton(
                key: ValueKey('delete-session-${widget.sessionId}'),
                onPressed: widget.onDelete,
                icon: Icon(
                  Icons.close,
                  size: 16,
                  color: _hovered
                      ? theme.colorScheme.error
                      : theme.colorScheme.outline,
                ),
                style: IconButton.styleFrom(
                  backgroundColor: _hovered ? theme.colorScheme.errorContainer : null,
                  visualDensity: VisualDensity.compact,
                ),
                tooltip: '删除会话',
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class OpencodeChatView extends StatefulWidget {
  const OpencodeChatView({
    super.key,
    required this.controller,
    required this.onToast,
  });

  final OpencodeController controller;
  final void Function(String text, {bool error}) onToast;

  @override
  State<OpencodeChatView> createState() => _OpencodeChatViewState();
}

class _OpencodeChatViewState extends State<OpencodeChatView> {
  OpencodeController get controller => widget.controller;
  final _inputController = TextEditingController();
  final _scrollController = ScrollController();

  bool _planCardDismissed = false;

  /// 按下 `/` 后展示的命令候选。
  List<OpencodeCommand> _slashSuggestions = const [];
  static const _builtinCommands = <OpencodeCommand>[
    OpencodeCommand(name: 'model', description: '切换模型'),
    OpencodeCommand(name: 'compact', description: '压缩会话历史'),
    OpencodeCommand(name: 'new', description: '新建会话'),
    OpencodeCommand(name: 'help', description: '查看全部命令'),
  ];

  @override
  void dispose() {
    _inputController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  void _onInputChanged(String value) {
    setState(() {
      _slashSuggestions = _matchSlashCommands(value);
    });
  }

  List<OpencodeCommand> _matchSlashCommands(String value) {
    if (!value.trim().startsWith('/')) return const [];
    final keyword = value.trim().substring(1).toLowerCase();
    final all = <OpencodeCommand>[
      ..._builtinCommands,
      for (final command in controller.commands)
        OpencodeCommand(name: command.name, description: command.description),
    ];
    return all
        .where(
          (command) =>
              keyword.isEmpty || command.name.toLowerCase().contains(keyword),
        )
        .toList();
  }

  void _clearInput() {
    _inputController.clear();
    setState(() {
      _slashSuggestions = const [];
    });
  }

  Future<void> _runSlashCommand(OpencodeCommand command) async {
    final messenger = ScaffoldMessenger.of(context);
    _clearInput();
    switch (command.name) {
      case 'model':
        await _pickModel();
        break;
      case 'compact':
        final ok = await controller.compactSession();
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              ok ? '会话已压缩' : '压缩失败：${controller.lastError ?? '未知错误'}',
            ),
          ),
        );
        break;
      case 'new':
        await controller.createSession();
        break;
      case 'help':
        _showHelpDialog();
        break;
      default:
        final ok = await controller.executeCommand(command.name);
        messenger.showSnackBar(
          SnackBar(
            content: Text(
              ok
                  ? '命令 /${command.name} 已执行'
                  : '命令 /${command.name} 失败：${controller.lastError ?? '未知错误'}',
            ),
          ),
        );
    }
  }

  Future<void> _pickModel() async {
    final models = controller.models;
    if (models.isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('没有可用模型')));
      return;
    }
    if (!mounted) return;
final selected = await showDialog<String?>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('选择模型'),
        content: SizedBox(
          width: double.maxFinite,
          child: RadioGroup<String>(
            groupValue: controller.selectedModelId,
            onChanged: (id) => Navigator.pop(context, id),
            child: ListView(
              shrinkWrap: true,
              children: [
                for (final model in models)
                  RadioListTile<String>(
                    value: model.id,
                    title: Text(model.label),
                    subtitle: Text(
                      model.isDefault
                          ? '默认'
                          : '${model.providerName}/${model.modelId}',
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
    if (selected != null) controller.selectModel(selected);
  }

  void _showHelpDialog() {
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('可用命令'),
        content: SizedBox(
          width: double.maxFinite,
          child: ListView(
            shrinkWrap: true,
            children: [
              for (final command in _allCommands())
                ListTile(
                  dense: true,
                  leading: const Icon(Icons.terminal, size: 18),
                  title: Text('/${command.name}'),
                  subtitle: command.description == null
                      ? null
                      : Text(command.description!),
                ),
            ],
          ),
        ),
      ),
    );
  }

  List<OpencodeCommand> _allCommands() => [
    ..._builtinCommands,
    for (final command in controller.commands)
      OpencodeCommand(name: command.name, description: command.description),
  ];

  Future<void> _pickAttachment() async {
    if (!mounted) return;
    final choice = await showModalBottomSheet<_AttachmentChoice>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.insert_drive_file_outlined),
              title: const Text('选择文件'),
              onTap: () =>
                  Navigator.pop(sheetContext, _AttachmentChoice.file),
            ),
            ListTile(
              leading: const Icon(Icons.folder_outlined),
              title: const Text('选择文件夹（展开为文件）'),
              onTap: () =>
                  Navigator.pop(sheetContext, _AttachmentChoice.directory),
            ),
          ],
        ),
      ),
    );
    if (choice == null || !mounted) return;
    switch (choice) {
      case _AttachmentChoice.file:
        final result = await FilePicker.platform.pickFiles(allowMultiple: true);
        final paths = result?.paths.whereType<String>().toList() ?? const [];
        for (final path in paths) {
          controller.addAttachment(path);
        }
        break;
      case _AttachmentChoice.directory:
        final dir = await FilePicker.platform.getDirectoryPath();
        if (dir == null) return;
        final files = await _expandDirectoryFiles(dir);
        if (files.isEmpty) {
          widget.onToast('文件夹内没有可选文件', error: true);
          return;
        }
        final selected = files.length > 10 ? files.sublist(0, 10) : files;
        if (files.length > 10) {
          if (!mounted) return;
          final proceed = await showDialog<bool>(
            context: context,
            builder: (dialogContext) => AlertDialog(
              title: const Text('文件数量较多'),
              content: Text('文件夹中有 ${files.length} 个文件，最多上传前 10 个（按名称排序）。'),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: const Text('取消'),
                ),
                FilledButton(
                  onPressed: () => Navigator.pop(dialogContext, true),
                  child: Text('添加前 10 个'),
                ),
              ],
            ),
          );
          if (proceed != true || !mounted) return;
        }
        for (final path in selected) {
          controller.addAttachment(path);
        }
        break;
    }
  }

  Future<List<String>> _expandDirectoryFiles(String directory) async {
    final result = <String>[];
    final root = Directory(directory);
    if (!await root.exists()) return result;
    try {
      await for (final entity
          in root.list(recursive: true, followLinks: false)) {
        if (entity is File) {
          result.add(entity.path);
          if (result.length >= 50) break;
        }
      }
    } catch (_) {}
    result.sort();
    return result;
  }

  Future<void> _previewMessageFile(OpencodeFileRef file) {
    final mime = file.mime ?? _fallbackMime(file.name);
    return _previewAttachment(
      OpencodeAttachment(path: file.path, name: file.name, mime: mime),
    );
  }

  static String _fallbackMime(String name) {
    final lower = name.toLowerCase();
    if (lower.endsWith('.md') || lower.endsWith('.txt')) return 'text/markdown';
    if (lower.endsWith('.docx')) {
      return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    }
    return 'application/octet-stream';
  }

  Future<void> _previewAttachment(OpencodeAttachment attachment) async {
    final file = File(attachment.path);
    if (!await file.exists()) {
      widget.onToast('附件不存在：${attachment.path}', error: true);
      return;
    }
    final mime = attachment.mime;
    final lower = attachment.name.toLowerCase();
    if (mime.startsWith('image/')) {
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(attachment.name),
          content: Image.file(
            file,
            errorBuilder: (context, error, stackTrace) =>
                const Text('无法预览该图片，请尝试用系统打开'),
          ),
          actions: [
            TextButton(
              onPressed: () => Process.start('cmd', ['/c', 'start', '', attachment.path]),
              child: const Text('用系统打开'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('关闭'),
            ),
          ],
        ),
      );
      return;
    }
    if (mime.startsWith('text/') || lower.endsWith('.md')) {
      try {
        final length = await file.length();
        if (length > 0 && length <= 200 * 1024) {
          final text = await file.readAsString();
          if (text.length <= 60000 && mounted) {
            await showDialog<void>(
              context: context,
              builder: (context) => AlertDialog(
                title: Text(attachment.name),
                content: SizedBox(
                  width: double.maxFinite,
                  child: SingleChildScrollView(
                    child: SelectableText(
                      text,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12,
                      ),
                    ),
                  ),
                ),
                actions: [
                  TextButton(
                    onPressed: () =>
                        Process.start('cmd', ['/c', 'start', '', attachment.path]),
                    child: const Text('用系统打开'),
                  ),
                  TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('关闭'),
                  ),
                ],
              ),
            );
            return;
          }
        }
      } catch (_) {}
    }
    if (lower.endsWith('.docx')) {
      try {
        final text = await extractDocxText(file);
        if (mounted && text.isNotEmpty) {
          await showDialog<void>(
            context: context,
            builder: (context) => AlertDialog(
              title: Text(attachment.name),
              content: SizedBox(
                width: double.maxFinite,
                child: SingleChildScrollView(
                  child: SelectableText(
                    text,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12,
                    ),
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () =>
                      Process.start('cmd', ['/c', 'start', '', attachment.path]),
                  child: const Text('用系统打开'),
                ),
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('关闭'),
                ),
              ],
            ),
          );
          return;
        }
      } catch (_) {
        // 结构损坏则落入系统兜底
      }
    }
    // 系统兜底：交给默认程序打开
    await Process.start('cmd', ['/c', 'start', '', attachment.path]);
  }

  Future<void> _send() async {
    final text = _inputController.text.trim();
    if (text.isEmpty || controller.sendingOwn) return;
    if (text.startsWith('/')) {
      // 例如 /model qwen 之类带参数的调用：直接匹配最接近的命令执行
      final parts = text.substring(1).split(' ');
      final name = parts.first.toLowerCase();
      final match = _allCommands()
          .where((command) => command.name == name)
          .toList();
      if (match.isNotEmpty) {
        await _runSlashCommand(match.first);
      } else {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('未知命令：$text（输入 / 查看可用命令）')));
        _clearInput();
      }
      return;
    }
    _inputController.clear();
    setState(() {
      _slashSuggestions = const [];
    });
    try {
      final sent = await controller.sendMessage(text);
      if (sent) {
        _scrollToBottom();
      } else if (controller.lastError != null) {
        if (!mounted) return;
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('发送失败：${controller.lastError}')));
      }
    } on ExternalBusyException catch (error) {
      // 会话被 opencode 终端占用：确认接管（将中止终端任务）后重试
      if (!mounted) return;
      final takeOver = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('opencode 终端正在处理该会话'),
          content: Text('$error\n\n点击"接管"将停止终端正在进行的任务，并在此继续发送。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('接管'),
            ),
          ],
        ),
      );
      if (takeOver == true) {
        final sent = await controller.sendMessage(text, takeover: true);
        if (sent) {
          _scrollToBottom();
        }
      } else {
        // 恢复输入（若已清空）
        _inputController.text = text;
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = controller.activeSession;
    final modelId = controller.selectedModelId;
    final modelLabel = controller.activeModelLabel;

    return Focus(
      onKeyEvent: (node, event) {
        if (event is KeyDownEvent &&
            event.logicalKey == LogicalKeyboardKey.tab) {
          controller.toggleAgentMode();
          widget.onToast(
            controller.planMode ? '已切换为 Plan 模式' : '已切换为 Build 模式',
          );
          return KeyEventResult.handled;
        }
        return KeyEventResult.ignored;
      },
      child: Column(
        children: [
          _SessionHeader(
            sessionName: session?.title ?? '未命名会话',
            models: controller.models,
            selectedModelId: modelId,
            modelLabel: modelLabel,
            sending: controller.sending,
            onModelChanged: controller.selectModel,
            onRefresh: () => controller.refresh(),
          ),
          Expanded(
            child: controller.loading
                ? const Center(child: CircularProgressIndicator())
                : _MessagesView(
                    messages: controller.messages,
                    scrollController: _scrollController,
                    onPreviewFile: _previewMessageFile,
                  ),
          ),
          _ConfirmationStack(
            controller: controller,
            planCardDismissed: _planCardDismissed,
            onDismissPlan: () => setState(() => _planCardDismissed = true),
            onToast: widget.onToast,
          ),
          _InputArea(
            controller: _inputController,
            sending: controller.sendingOwn,
            externalBusy: controller.externalBusyForActive,
            enabled: controller.serverReachable,
            onChanged: _onInputChanged,
            slashSuggestions: _slashSuggestions,
            onSelectCommand: _runSlashCommand,
            onSend: _send,
            onStop: controller.stop,
            attachments: controller.attachments,
            onAddAttachment: _pickAttachment,
            onRemoveAttachment: controller.removeAttachment,
            onPreviewAttachment: _previewAttachment,
            agentMode: controller.agentMode,
            planMode: controller.planMode,
            onToggleAgentMode: controller.toggleAgentMode,
            modelLabel: modelLabel ?? modelId ?? '未选择模型',
          ),
        ],
      ),
    );
  }
}

class _MessagesView extends StatelessWidget {
  const _MessagesView({
    required this.messages,
    required this.scrollController,
    required this.onPreviewFile,
  });

  final List<OpencodeMessage> messages;
  final ScrollController scrollController;
  final ValueChanged<OpencodeFileRef> onPreviewFile;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (messages.isEmpty) {
      return Center(
        child: Text('还没有对话，发一条消息开始吧', style: theme.textTheme.bodySmall),
      );
    }
    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.all(16),
      itemCount: messages.length,
      itemBuilder: (context, index) {
        final message = messages[index];
        final isUser = message.role == 'user';
        return Align(
          alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
          child: Container(
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.all(10),
            constraints: const BoxConstraints(maxWidth: 640),
            decoration: BoxDecoration(
              color: isUser
                  ? theme.colorScheme.primaryContainer
                  : theme.colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (message.modelLabel != null)
                  Padding(
                    padding: const EdgeInsets.only(right: 32),
                    child: Text(
                      message.modelLabel!,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.outline,
                      ),
                    ),
                  ),
                const SizedBox(height: 2),
                _MessageContent(
                  content: message.content,
                  files: message.files,
                  onPreviewFile: onPreviewFile,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _MessageContent extends StatelessWidget {
  const _MessageContent({
    required this.content,
    required this.files,
    required this.onPreviewFile,
  });

  final String content;
  final List<OpencodeFileRef> files;
  final ValueChanged<OpencodeFileRef> onPreviewFile;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final mermaidMatch =
        RegExp(r'```mermaid\s*\n([\s\S]*?)```').firstMatch(content);
    String? mermaidCode;
    var text = content.trim();
    if (mermaidMatch != null) {
      mermaidCode = mermaidMatch.group(1)!.trim();
      text = content.replaceRange(mermaidMatch.start, mermaidMatch.end, '').trim();
    }
    final hasContent = text.isNotEmpty || mermaidCode != null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (mermaidCode != null) ...[
          _MermaidBlock(source: mermaidCode),
          if (text.isNotEmpty) const SizedBox(height: 6),
        ],
        if (text.isNotEmpty)
          SelectableText(text, style: theme.textTheme.bodyMedium),
        if (files.isNotEmpty) ...[
          if (hasContent) const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final file in files)
                ActionChip(
                  avatar: Icon(_fileIcon(file), size: 16),
                  label: Text(
                    file.name,
                    style: theme.textTheme.labelSmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                  onPressed: () => onPreviewFile(file),
                  tooltip: file.path,
                ),
            ],
          ),
        ],
        if (!hasContent && files.isEmpty)
          SelectableText('（空回复）', style: theme.textTheme.bodyMedium),
      ],
    );
  }
}

IconData _fileIcon(OpencodeFileRef file) {
  final name = file.name.toLowerCase();
  if (name.endsWith('.md') || name.endsWith('.txt')) {
    return Icons.description_outlined;
  }
  if (name.endsWith('.docx') || name.endsWith('.doc')) {
    return Icons.article_outlined;
  }
  if (RegExp(r'\.(png|jpe?g|gif|webp|bmp|svg)$').hasMatch(name)) {
    return Icons.image_outlined;
  }
  if (RegExp(r'\.(py|js|ts|dart|java|c|cpp|go|rs|json|yaml|yml|xml|html|css)$')
      .hasMatch(name)) {
    return Icons.code;
  }
  return Icons.insert_drive_file_outlined;
}

class _MermaidBlock extends StatelessWidget {
  const _MermaidBlock({required this.source});

  final String source;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      key: const ValueKey('mermaid-diagram'),
      onTap: () => _openPreview(context),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: MermaidDiagram(source: source),
      ),
    );
  }

  Future<void> _openPreview(BuildContext context) {
    return showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.account_tree_outlined, size: 20),
            SizedBox(width: 8),
            Text('架构图'),
          ],
        ),
        content: SizedBox(
          width: 680,
          height: 420,
          child: SingleChildScrollView(
            child: InteractiveViewer(
              minScale: 0.2,
              maxScale: 4,
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: MermaidDiagram(source: source),
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('关闭'),
          ),
        ],
      ),
    );
  }
}

class _SessionHeader extends StatelessWidget {
  const _SessionHeader({
    required this.sessionName,
    required this.models,
    required this.selectedModelId,
    required this.modelLabel,
    required this.sending,
    required this.onModelChanged,
    required this.onRefresh,
  });

  final String sessionName;
  final List<OpencodeModel> models;
  final String? selectedModelId;
  final String? modelLabel;
  final bool sending;
  final ValueChanged<String?> onModelChanged;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
      decoration: BoxDecoration(
        border: Border(bottom: BorderSide(color: theme.dividerColor)),
      ),
      child: Row(
        children: [
          Expanded(
            child: Text(
              sessionName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleMedium,
            ),
          ),
          if (models.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(right: 4),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String?>(
                  key: const ValueKey('model-dropdown'),
                  value: selectedModelId,
                  items: [
                    for (final model in models)
                      DropdownMenuItem(
                        value: model.id,
                        child: Tooltip(
                          message: model.label,
                          child: Text(
                            '${model.providerName}/${model.modelId}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.labelMedium,
                          ),
                        ),
                      ),
                  ],
                  onChanged: sending ? null : onModelChanged,
                  hint: const Text('选择模型'),
                ),
              ),
            ),
          if (modelLabel != null)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: theme.colorScheme.secondaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(modelLabel!, style: theme.textTheme.labelSmall),
              ),
            ),
          IconButton(
            key: const ValueKey('refresh-button'),
            onPressed: onRefresh,
            icon: const Icon(Icons.refresh, size: 20),
            tooltip: '刷新会话',
          ),
        ],
      ),
    );
  }
}

class _InputArea extends StatelessWidget {
  const _InputArea({
    required this.controller,
    required this.sending,
    this.externalBusy = false,
    required this.enabled,
    required this.onChanged,
    required this.slashSuggestions,
    required this.onSelectCommand,
    required this.onSend,
    required this.onStop,
    required this.attachments,
    required this.onAddAttachment,
    required this.onRemoveAttachment,
    required this.onPreviewAttachment,
    required this.agentMode,
    required this.planMode,
    required this.onToggleAgentMode,
    required this.modelLabel,
  });

  final TextEditingController controller;
  final bool sending;

  /// 当前会话被 opencode 终端占用（非本端发起）——展示琥珀色提示横幅。
  final bool externalBusy;
  final bool enabled;
  final ValueChanged<String> onChanged;
  final List<OpencodeCommand> slashSuggestions;
  final ValueChanged<OpencodeCommand> onSelectCommand;
  final VoidCallback onSend;
  final Future<bool> Function() onStop;
  final List<OpencodeAttachment> attachments;
  final VoidCallback onAddAttachment;
  final void Function(int index) onRemoveAttachment;
  final void Function(OpencodeAttachment attachment) onPreviewAttachment;
  final String agentMode;
  final bool planMode;
  final VoidCallback onToggleAgentMode;
  final String modelLabel;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: theme.dividerColor)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (slashSuggestions.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              constraints: const BoxConstraints(maxHeight: 200),
              child: Card(
                margin: EdgeInsets.zero,
                child: ListView(
                  shrinkWrap: true,
                  children: [
                    for (final command in slashSuggestions)
                      ListTile(
                        dense: true,
                        leading: const Icon(Icons.terminal, size: 18),
                        title: Text('/${command.name}'),
                        subtitle: command.description == null
                            ? null
                            : Text(command.description!),
                        onTap: () => onSelectCommand(command),
                      ),
                  ],
                ),
              ),
            ),
          if (attachments.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
              decoration: BoxDecoration(
                color: theme.colorScheme.surface,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: theme.dividerColor),
              ),
              child: Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (var index = 0; index < attachments.length; index++)
                    InputChip(
                      avatar: const Icon(Icons.attach_file, size: 16),
                      label: Text(attachments[index].name),
                      onPressed: () => onPreviewAttachment(attachments[index]),
                      onDeleted: () => onRemoveAttachment(index),
                      deleteButtonTooltipMessage: '移除附件',
                      visualDensity: VisualDensity.compact,
                    ),
                ],
              ),
            ),
          if (externalBusy)
            Container(
              key: const ValueKey('external-busy-banner'),
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                color: theme.colorScheme.tertiaryContainer,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Icon(
                    Icons.sync_problem,
                    size: 16,
                    color: theme.colorScheme.onTertiaryContainer,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'opencode 终端正在处理该会话，发送消息将提示是否接管',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              IconButton(
                key: const ValueKey('add-attachment-button'),
                tooltip: '添加附件（文件/文件夹）',
                onPressed: enabled && !sending ? onAddAttachment : null,
                icon: const Icon(Icons.add),
                visualDensity: VisualDensity.compact,
              ),
              const SizedBox(width: 4),
              Expanded(
                child: TextField(
                  key: const ValueKey('message-input'),
                  controller: controller,
                  minLines: 1,
                  maxLines: 4,
                  enabled: enabled && !sending,
                  onChanged: onChanged,
                  onSubmitted: (_) => onSend(),
                  decoration: const InputDecoration(
                    hintText: '输入消息，回车发送；输入 / 查看命令；Tab 切换 Plan/Build',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              if (sending)
                IconButton(
                  key: const ValueKey('stop-button'),
                  tooltip: '停止生成',
                  onPressed: onStop,
                  icon: Icon(
                    Icons.stop_circle_outlined,
                    size: 22,
                    color: theme.colorScheme.error,
                  ),
                )
              else
                FilledButton(
                  key: const ValueKey('send-button'),
                  onPressed: enabled ? onSend : null,
                  child: const Icon(Icons.send),
                ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              InkWell(
                key: const ValueKey('agent-mode-toggle'),
                onTap: enabled ? onToggleAgentMode : null,
                borderRadius: BorderRadius.circular(6),
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 3,
                  ),
                  decoration: BoxDecoration(
                    color: planMode
                        ? theme.colorScheme.primaryContainer
                        : theme.colorScheme.surfaceContainerHighest,
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        planMode ? Icons.assignment_outlined : Icons.build_outlined,
                        size: 14,
                        color: planMode
                            ? theme.colorScheme.onPrimaryContainer
                            : theme.colorScheme.onSurfaceVariant,
                      ),
                      const SizedBox(width: 4),
                      Text(
                        'Plan',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: planMode
                              ? theme.colorScheme.onPrimaryContainer
                              : theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        '/',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: theme.colorScheme.outline,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Icon(
                        !planMode ? Icons.assignment_outlined : Icons.build_outlined,
                        size: 14,
                        color: !planMode
                            ? theme.colorScheme.onPrimaryContainer
                            : theme.colorScheme.onSurfaceVariant,
                      ),
                      const SizedBox(width: 4),
                      Text(
                        'Build',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: !planMode
                              ? theme.colorScheme.onPrimaryContainer
                              : theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  modelLabel,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.outline,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

enum _AttachmentChoice { file, directory }

/// 居中确认卡片区：权限 > 问题 > 方案（Plan）依次优先。
class _ConfirmationStack extends StatelessWidget {
  const _ConfirmationStack({
    required this.controller,
    required this.planCardDismissed,
    required this.onDismissPlan,
    required this.onToast,
  });

  final OpencodeController controller;
  final bool planCardDismissed;
  final VoidCallback onDismissPlan;
  final void Function(String text, {bool error}) onToast;

  bool get _sessionInPlanMode {
    if (controller.activeSession?.agent == 'plan') return true;
    for (final message in controller.messages) {
      if (message.agent == 'plan') return true;
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    final permission = controller.pendingPermission;
    if (permission != null) {
      return _PermissionCard(
        request: permission,
        controller: controller,
        onToast: onToast,
      );
    }
    final question = controller.pendingQuestion;
    if (question != null) {
      return _QuestionCard(
        request: question,
        controller: controller,
        onToast: onToast,
      );
    }
    if (!planCardDismissed && _sessionInPlanMode) {
      return _PlanCard(
        controller: controller,
        onDismiss: onDismissPlan,
        onToast: onToast,
      );
    }
    return const SizedBox.shrink();
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({
    required this.controller,
    required this.onDismiss,
    required this.onToast,
  });

  final OpencodeController controller;
  final VoidCallback onDismiss;
  final void Function(String text, {bool error}) onToast;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.assignment_outlined,
                  size: 18,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 8),
                Text('当前处于 Plan 模式', style: theme.textTheme.titleSmall),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'OpenCode 会先规划再执行。确认方案后可以切到 Build 直接执行，或保持 Plan 继续调整。',
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 12),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: onDismiss,
                  child: const Text('保持 Plan'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: () {
                    controller.setAgentMode('build');
                    onDismiss();
                    onToast('已切换到 Build 模式');
                  },
                  icon: const Icon(Icons.build_outlined, size: 16),
                  label: const Text('切到 Build 执行'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PermissionCard extends StatelessWidget {
  const _PermissionCard({
    required this.request,
    required this.controller,
    required this.onToast,
  });

  final OpencodePermissionRequest request;
  final OpencodeController controller;
  final void Function(String text, {bool error}) onToast;

  static const _known = {'一次允许', '总是允许', '拒绝'};

  String _patternSummary() {
    final parts = <String>[];
    if (request.patterns.isNotEmpty) {
      parts.add(request.patterns.join(' '));
    }
    if (request.tool != null) {
      final toolName = request.tool!['callID'];
      if (toolName is String) parts.add('callID: $toolName');
    }
    return parts.isEmpty ? request.permission : '${request.permission}\n${parts.join('\n')}';
  }

  void _confirm(List<List<String>> answers) {
    final selected = answers.isEmpty ? const <String>[] : answers.first;
    final String decision;
    if (selected.contains('拒绝')) {
      decision = OpencodePermissionDecision.reject;
    } else if (selected.contains('总是允许')) {
      decision = OpencodePermissionDecision.always;
    } else {
      decision = OpencodePermissionDecision.once;
    }
    final message = selected.where((item) => !_known.contains(item)).join(' ');
    controller.replyPermission(decision: decision, message: message.isNotEmpty ? message : null);
  }

  @override
  Widget build(BuildContext context) {
    return _CardWrap(
      child: ConfirmWizard(
        title: '权限请求',
        subtitle: _patternSummary(),
        steps: [
          ChoiceStep(
            question: '允许此操作吗？',
            options: ['一次允许', '总是允许', '拒绝'],
            allowCustom: true,
            customHint: '附加说明（可选）',
          ),
        ],
        onConfirm: _confirm,
        onSkip: () => controller.replyPermission(
          decision: OpencodePermissionDecision.reject,
        ),
        confirmLabel: '确认',
      ),
    );
  }
}

class _QuestionCard extends StatelessWidget {
  const _QuestionCard({
    required this.request,
    required this.controller,
    required this.onToast,
  });

  final OpencodeQuestionRequest request;
  final OpencodeController controller;
  final void Function(String text, {bool error}) onToast;

  @override
  Widget build(BuildContext context) {
    return _CardWrap(
      child: ConfirmWizard(
        title: '选择确认',
        subtitle: '请依次回答以下 ${request.questions.length} 个问题',
        steps: [
          for (final question in request.questions)
            ChoiceStep(
              question: question.question,
              options: [
                for (final option in question.options) option.label,
              ],
              multiple: question.multiple,
              allowCustom: question.custom,
              customHint: '自己输入回答',
            ),
        ],
        onConfirm: (answers) => controller.answerQuestion(answers),
        onSkip: () => controller.skipQuestion(),
        confirmLabel: request.questions.length > 1 ? '提交全部' : '确认',
      ),
    );
  }
}

class _CardWrap extends StatelessWidget {
  const _CardWrap({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8),
      child: child,
    );
  }
}
