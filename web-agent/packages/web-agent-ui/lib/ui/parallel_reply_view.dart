import 'package:flutter/material.dart';

import '../controller/app_controller.dart';
import '../models/schema.dart';

class ParallelReplyView extends StatelessWidget {
  const ParallelReplyView({
    super.key,
    required this.turn,
    required this.controller,
  });

  final ChatTurn turn;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final replies = turn.replies;
    if (replies.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            SizedBox(width: 8),
            Text('等待回复…'),
          ],
        ),
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = replies.length == 1 ? 1 : 2;
        final itemWidth = (constraints.maxWidth - (columns - 1) * 12) / columns;
        return Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final reply in replies)
              SizedBox(width: itemWidth, child: _ReplyCard(message: reply)),
          ],
        );
      },
    );
  }
}

class _ReplyCard extends StatelessWidget {
  const _ReplyCard({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final Color color;
    switch (message.status) {
      case 'received':
        color = theme.colorScheme.primary;
      case 'error':
        color = theme.colorScheme.error;
      default:
        color = theme.colorScheme.outline;
    }
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(Icons.smart_toy_outlined, size: 16, color: color),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    message.modelName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelLarge,
                  ),
                ),
                if (message.status == 'pending')
                  const Padding(
                    padding: EdgeInsets.only(left: 6),
                    child: SizedBox(
                      width: 12,
                      height: 12,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            SelectableText(
              message.content,
              style: theme.textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}