class AtMentionMatch {
  const AtMentionMatch({required this.start, required this.end});

  final int start;

  /// 插入点（光标位置）
  final int end;

  int get length => end - start;
}

/// 解析输入文本中的 `@model` 片段。
class AtMentionParser {
  static final RegExp _nameChar = RegExp(r'[A-Za-z0-9_\-./]');

  /// 提取全文所有 mention（不含 `@`），用于发送时解析模型列表。
  static List<String> extractMentions(String text) {
    final results = <String>[];
    final buffer = StringBuffer();
    var inMention = false;
    for (final rune in text.runes) {
      final char = String.fromCharCode(rune);
      if (char == '@') {
        if (inMention) {
          final token = buffer.toString();
          if (token.isNotEmpty) results.add(token);
        }
        inMention = true;
        buffer.clear();
        continue;
      }
      if (!inMention) continue;
      if (_nameChar.hasMatch(char)) {
        buffer.write(char);
        continue;
      }
      if (inMention) {
        final token = buffer.toString();
        if (token.isNotEmpty) results.add(token);
        inMention = false;
        buffer.clear();
      }
    }
    if (inMention && buffer.isNotEmpty) {
      results.add(buffer.toString());
    }
    return results;
  }

  /// 返回光标前最后一个正在输入的 mention（不含 `@`）。
  ///
  /// 规则：光标前的最后一个 `@` 到光标之间必须只包含名称字符
  /// （无空格、无第二个 `@`），否则不认为在输入 mention。
  static AtMentionMatch? findActiveMention(String text, int cursor) {
    if (cursor <= 0 || text.isEmpty || cursor > text.length) return null;
    var index = cursor - 1;
    while (index >= 0 && _nameChar.hasMatch(text[index])) {
      index--;
    }
    if (index < 0 || text[index] != '@') return null;
    final start = index;
    if (start >= cursor) return null;
    return AtMentionMatch(start: start, end: cursor);
  }

  /// 用选中的模型ID替换光标前的一段 mention。
  static String applySuggestion(String text, int cursor, String modelId) {
    final match = findActiveMention(text, cursor);
    if (match == null) return text;
    final before = text.substring(0, match.start);
    final after = text.substring(match.end);
    return '$before@$modelId$after';
  }
}