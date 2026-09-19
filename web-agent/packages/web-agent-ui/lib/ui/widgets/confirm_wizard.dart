import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 单步选项：单/多选 + 可选"自己输入"。
class ChoiceStep {
  const ChoiceStep({
    required this.question,
    required this.options,
    this.multiple = false,
    this.allowCustom = true,
    this.customHint,
  });

  final String question;
  final List<String> options;
  final bool multiple;
  final bool allowCustom;
  final String? customHint;
}

/// 通用确认向导：选项列表（首个恒定标记「推荐」）+ 自己输入 + 底部 ◀/▶ 步骤切换 + 确认。
///
/// 每一步的结果是该步选中的标签 + 自定义输入（若填了「自己输入」）。
/// [onConfirm] 收到所有步骤的答案，顺序与 [steps] 一致。
class ConfirmWizard extends StatefulWidget {
  const ConfirmWizard({
    super.key,
    required this.title,
    this.subtitle,
    required this.steps,
    required this.onConfirm,
    this.onSkip,
    this.confirmLabel = '确认',
    this.skipLabel = '跳过',
  });

  final String title;
  final String? subtitle;
  final List<ChoiceStep> steps;
  final ValueChanged<List<List<String>>> onConfirm;
  final VoidCallback? onSkip;
  final String confirmLabel;
  final String? skipLabel;

  @override
  State<ConfirmWizard> createState() => _ConfirmWizardState();
}

class _ConfirmWizardState extends State<ConfirmWizard> {
  late final List<Set<String>> _selected;
  late final List<TextEditingController> _customControllers;
  late final List<bool> _customUsed;
  late int _step;

  @override
  void initState() {
    super.initState();
    _step = 0;
    _selected = [
      for (final _ in widget.steps) <String>{},
    ];
    _customControllers = [
      for (var index = 0; index < widget.steps.length; index++)
        TextEditingController(),
    ];
    _customUsed = List.filled(widget.steps.length, false);
  }

  @override
  void dispose() {
    for (final controller in _customControllers) {
      controller.dispose();
    }
    super.dispose();
  }

  ChoiceStep get _current => widget.steps[_step];

  bool get _canConfirm {
    if (_selected[_step].isNotEmpty) return true;
    return _customUsed[_step] && _customControllers[_step].text.trim().isNotEmpty;
  }

  void _go(int delta) {
    final next = (_step + delta).clamp(0, widget.steps.length - 1);
    if (next != _step) setState(() => _step = next);
  }

  void _toggleOption(String label) {
    setState(() {
      if (_current.multiple) {
        final set = _selected[_step];
        if (set.contains(label)) {
          set.remove(label);
        } else {
          set.add(label);
        }
      } else {
        _selected[_step] = <String>{label};
      }
    });
  }

  void _confirm() {
    final answers = <List<String>>[];
    for (var index = 0; index < widget.steps.length; index++) {
      final result = <String>[..._selected[index]];
      final custom = _customControllers[index].text.trim();
      if (_customUsed[index] && custom.isNotEmpty) result.add(custom);
      answers.add(result);
    }
    widget.onConfirm(answers);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final step = _current;
    return Focus(
      autofocus: true,
      onKeyEvent: (node, event) {
        if (event is KeyDownEvent) {
          if (event.logicalKey == LogicalKeyboardKey.arrowLeft) {
            _go(-1);
            return KeyEventResult.handled;
          }
          if (event.logicalKey == LogicalKeyboardKey.arrowRight) {
            _go(1);
            return KeyEventResult.handled;
          }
        }
        return KeyEventResult.ignored;
      },
      child: Card(
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.title,
                      style: theme.textTheme.titleSmall?.copyWith(
                        color: theme.colorScheme.primary,
                      ),
                    ),
                  ),
                  if (widget.steps.length > 1)
                    Text(
                      '${_step + 1} / ${widget.steps.length}',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.outline,
                      ),
                    ),
                ],
              ),
              if (widget.subtitle != null) ...[
                const SizedBox(height: 2),
                Text(widget.subtitle!, style: theme.textTheme.bodySmall),
              ],
              const SizedBox(height: 8),
              Text(step.question, style: theme.textTheme.bodyMedium),
              const SizedBox(height: 8),
              for (var index = 0; index < step.options.length; index++)
                _OptionTile(
                  label: step.options[index],
                  recommended: index == 0,
                  selected: _selected[_step].contains(step.options[index]),
                  multiple: step.multiple,
                  onTap: () => _toggleOption(step.options[index]),
                ),
              if (step.allowCustom)
                _CustomInputTile(
                  controller: _customControllers[_step],
                  used: _customUsed[_step],
                  hint: step.customHint,
                  onChanged: (used) {
                    setState(() => _customUsed[_step] = used);
                  },
                  onCommit: _confirm,
                ),
              const SizedBox(height: 8),
              Row(
                children: [
                  if (widget.onSkip != null)
                    TextButton(
                      onPressed: widget.onSkip,
                      child: Text(widget.skipLabel ?? '跳过'),
                    ),
                  const Spacer(),
                  IconButton(
                    tooltip: '上一步（←）',
                    onPressed: _step > 0 ? () => _go(-1) : null,
                    icon: const Icon(Icons.chevron_left),
                    visualDensity: VisualDensity.compact,
                  ),
                  const SizedBox(width: 4),
                  IconButton(
                    tooltip: '下一步（→）',
                    onPressed: _step < widget.steps.length - 1 && _canConfirm
                        ? () => _go(1)
                        : null,
                    icon: const Icon(Icons.chevron_right),
                    visualDensity: VisualDensity.compact,
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: _canConfirm ? _confirm : null,
                    child: Text(widget.confirmLabel),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _OptionTile extends StatelessWidget {
  const _OptionTile({
    required this.label,
    required this.recommended,
    required this.selected,
    required this.multiple,
    required this.onTap,
  });

  final String label;
  final bool recommended;
  final bool selected;
  final bool multiple;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Material(
        color: selected
            ? theme.colorScheme.secondaryContainer
            : theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              children: [
                Icon(
                  multiple
                      ? (selected
                          ? Icons.check_box
                          : Icons.check_box_outline_blank)
                      : (selected
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked),
                  size: 18,
                  color: selected
                      ? theme.colorScheme.primary
                      : theme.colorScheme.outline,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(label, style: theme.textTheme.bodyMedium),
                ),
                if (recommended)
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 6,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.primaryContainer,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '推荐',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onPrimaryContainer,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CustomInputTile extends StatelessWidget {
  const _CustomInputTile({
    required this.controller,
    required this.used,
    required this.hint,
    required this.onChanged,
    required this.onCommit,
  });

  final TextEditingController controller;
  final bool used;
  final String? hint;
  final ValueChanged<bool> onChanged;
  final VoidCallback onCommit;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        CheckboxListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          value: used,
          onChanged: (value) {
            onChanged(value ?? false);
            if (value != null && value) {
              controller.clear();
            }
          },
          controlAffinity: ListTileControlAffinity.leading,
          title: Text('自己输入', style: theme.textTheme.bodySmall),
        ),
        if (used)
          TextField(
            controller: controller,
            autofocus: true,
            minLines: 1,
            maxLines: 3,
            decoration: InputDecoration(
              hintText: hint ?? '输入自定义内容',
              isDense: true,
              border: const OutlineInputBorder(),
            ),
            onSubmitted: (_) => onCommit(),
          ),
      ],
    );
  }
}