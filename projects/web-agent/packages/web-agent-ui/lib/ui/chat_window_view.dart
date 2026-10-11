import 'package:flutter/material.dart';

import '../controller/app_controller.dart';
import '../logic/at_mention_parser.dart';
import '../models/model_catalog.dart';
import '../models/schema.dart';
import '../services/daemon_client.dart';
import 'parallel_reply_view.dart';

class ChatWindowView extends StatefulWidget {
  const ChatWindowView({
    super.key,
    required this.window,
    required this.controller,
  });

  final ChatWindow window;
  final AppController controller;

  @override
  State<ChatWindowView> createState() => _ChatWindowViewState();
}

class _ChatWindowViewState extends State<ChatWindowView> {
  final _inputController = TextEditingController();
  final _scrollController = ScrollController();
  final _focusNode = FocusNode();

  String _draft = '';
  List<String> _mentionSuggestions = const [];
  bool _mentionActive = false;

  @override
  void dispose() {
    _inputController.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _draft = value;
    final match = AtMentionParser.findActiveMention(value, _inputController.selection.baseOffset);
    if (match != null) {
      _mentionActive = true;
      final keyword = value.substring(match.start + 1, match.end).toLowerCase();
      _mentionSuggestions = _filterSuggestions(keyword);
    } else {
      _mentionActive = false;
      _mentionSuggestions = const [];
    }
    setState(() {});
  }

  List<String> _filterSuggestions(String keyword) {
    final result = <String>[];
    for (final model in webAgentModels) {
      if (keyword.isEmpty || model.id.contains(keyword) || model.name.contains(keyword)) {
        result.add(model.id);
      }
    }
    return result;
  }

  void _applySuggestion(String modelId) {
    final cursor = _inputController.selection.baseOffset;
    final updated = AtMentionParser.applySuggestion(_draft, cursor, modelId);
    _inputController.value = TextEditingValue(
      text: updated,
      selection: TextSelection.collapsed(offset: updated.length),
    );
    _draft = updated;
    _mentionActive = false;
    _mentionSuggestions = const [];
    setState(() {});
  }

  Future<void> _send() async {
    final text = _draft.trim();
    if (text.isEmpty || widget.controller.sending) return;
    final mentions = AtMentionParser.extractMentions(text).toSet().toList();
    final cleanText = _stripMentions(text, mentions);
    if (cleanText.trim().isEmpty && mentions.isEmpty) return;
    try {
      await widget.controller.sendMessage(widget.window.id, cleanText, mentions);
      _inputController.clear();
      _draft = '';
      _mentionActive = false;
      setState(() {});
      _scrollToBottom();
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('发送失败：$error')));
    }
  }

  String _stripMentions(String text, List<String> mentions) {
    var result = text;
    for (final mention in mentions) {
      result = result.replaceAll('@$mention', '').trim();
    }
    return result;
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final windowId = widget.window.id;
    final turns = controller.turnsFor(windowId);
    final selected = controller.selectedModelsFor(windowId);
    final daemon = controller.daemonStatus;
    final running = controller.sending || controller.runningFor(windowId);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _Header(window: widget.window, controller: controller),
        _DaemonBanner(daemonStatus: daemon),
        Expanded(
          child: turns.isEmpty
              ? _EmptyState(controller: controller, focusNode: _focusNode)
              : ListView.builder(
                  controller: _scrollController,
                  padding: const EdgeInsets.all(16),
                  itemCount: turns.length,
                  itemBuilder: (context, index) => _TurnView(
                    turn: turns[index],
                    controller: controller,
                  ),
                ),
        ),
        _ModelBar(
          models: selected,
          allModels: controller.allModels(),
          onToggle: (modelId) => controller.toggleModel(windowId, modelId),
        ),
        _InputArea(
          controller: _inputController,
          focusNode: _focusNode,
          sending: controller.sending,
          running: running,
          onStop: () => controller.stopTask(windowId),
          mentionActive: _mentionActive,
          suggestions: _mentionSuggestions,
          onChanged: _onChanged,
          onSelectSuggestion: _applySuggestion,
          onSend: _send,
        ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.window, required this.controller});

  final ChatWindow window;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
      child: Row(
        children: [
          Expanded(
            child: Text(
              window.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleMedium,
            ),
          ),
          IconButton(
            tooltip: '刷新连接',
            onPressed: () => controller.refreshDaemonStatus(),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
    );
  }
}

class _DaemonBanner extends StatelessWidget {
  const _DaemonBanner({required this.daemonStatus});

  final DaemonStatus? daemonStatus;

  @override
  Widget build(BuildContext context) {
    final status = daemonStatus;
    final Color color;
    final String text;
    if (status == null) {
      color = Colors.grey;
      text = '正在检测本地守护进程…';
    } else if (!status.reachable) {
      color = Colors.redAccent;
      text = '守护进程未运行，请先运行 tools/start-ui.ps1 启动';
    } else if (!status.extensionConnected) {
      color = Colors.orange;
      text = '守护进程已连接，但 Web Agent 扩展未连接（请打开扩展并开启“本机智能体控制”）';
    } else {
      color = Colors.green;
      text = '已连接 daemon ${status.extensionVersion ?? ''} · 挂起任务 ${status.pending}';
    }
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(Icons.circle, size: 10, color: color),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: TextStyle(fontSize: 12, color: color))),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.controller, required this.focusNode});

  final AppController controller;
  final FocusNode focusNode;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return InkWell(
      onTap: () => focusNode.requestFocus(),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.forum_outlined, size: 48, color: theme.colorScheme.outline),
            const SizedBox(height: 12),
            Text('选择底部模型，或输入 @模型 后发送', style: theme.textTheme.bodyMedium),
          ],
        ),
      ),
    );
  }
}

class _TurnView extends StatelessWidget {
  const _TurnView({required this.turn, required this.controller});

  final ChatTurn turn;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _UserMessage(message: turn.userMessage),
        const SizedBox(height: 8),
        ParallelReplyView(turn: turn, controller: controller),
        const SizedBox(height: 16),
      ],
    );
  }
}

class _UserMessage extends StatelessWidget {
  const _UserMessage({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.only(left: 48),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: theme.colorScheme.primaryContainer,
          borderRadius: BorderRadius.circular(12),
        ),
        child: SelectableText(
          message.content,
          style: TextStyle(color: theme.colorScheme.onPrimaryContainer),
        ),
      ),
    );
  }
}

class _ModelBar extends StatelessWidget {
  const _ModelBar({
    required this.models,
    required this.allModels,
    required this.onToggle,
  });

  final List<String> models;
  final List<AiModel> allModels;
  final ValueChanged<String> onToggle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: theme.dividerColor),
        ),
      ),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text('模型', style: theme.textTheme.labelMedium),
          for (final model in allModels)
            FilterChip(
              label: Text(model.name),
              selected: models.contains(model.id),
              onSelected: (_) => onToggle(model.id),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}

class _InputArea extends StatelessWidget {
  const _InputArea({
    required this.controller,
    required this.focusNode,
    required this.sending,
    required this.running,
    required this.onStop,
    required this.mentionActive,
    required this.suggestions,
    required this.onChanged,
    required this.onSelectSuggestion,
    required this.onSend,
  });

  final TextEditingController controller;
  final FocusNode focusNode;
  final bool sending;
  final bool running;
  final VoidCallback onStop;
  final bool mentionActive;
  final List<String> suggestions;
  final ValueChanged<String> onChanged;
  final ValueChanged<String> onSelectSuggestion;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: theme.dividerColor),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (mentionActive && suggestions.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              constraints: const BoxConstraints(maxHeight: 160),
              child: Card(
                margin: EdgeInsets.zero,
                child: ListView(
                  shrinkWrap: true,
                  children: [
                    for (final suggestion in suggestions)
                      ListTile(
                        dense: true,
                        leading: const Icon(Icons.smart_toy_outlined, size: 18),
                        title: Text('@$suggestion'),
                        onTap: () => onSelectSuggestion(suggestion),
                      ),
                  ],
                ),
              ),
            ),
          TextField(
            controller: controller,
            focusNode: focusNode,
            maxLines: 4,
            minLines: 1,
            onChanged: onChanged,
            decoration: InputDecoration(
              hintText: '输入消息，@模型 指定模型…',
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
            ),
            onSubmitted: (_) {
              if (!sending) onSend();
            },
          ),
          const SizedBox(height: 8),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              if (running)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: OutlinedButton.icon(
                    onPressed: sending ? null : onStop,
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Theme.of(context).colorScheme.error,
                      visualDensity: VisualDensity.compact,
                    ),
                    icon: const Icon(Icons.stop_circle_outlined, size: 18),
                    label: const Text('停止'),
                  ),
                ),
              FilledButton.icon(
                onPressed: sending ? null : onSend,
                icon: sending
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send),
                label: const Text('发送'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}