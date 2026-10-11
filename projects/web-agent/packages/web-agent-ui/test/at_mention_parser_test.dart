import 'package:flutter_test/flutter_test.dart';
import 'package:web_agent_ui/logic/at_mention_parser.dart';

void main() {
  group('AtMentionParser.extractMentions', () {
    test('提取单个 mention', () {
      expect(AtMentionParser.extractMentions('@deepseek 你好'), ['deepseek']);
    });

    test('提取多个 mention', () {
      expect(
        AtMentionParser.extractMentions('@deepseek 和 @doubao 请回答'),
        ['deepseek', 'doubao'],
      );
    });

    test('原文不含 mention 返回空', () {
      expect(AtMentionParser.extractMentions('你好世界'), isEmpty);
    });

    test('无 @ 的网址不被误判', () {
      expect(AtMentionParser.extractMentions('参考 https://x.com'), isNot(contains('x.com')));
    });
  });

  group('AtMentionParser.findActiveMention', () {
    test('光标在词尾时识别 mention', () {
      final match = AtMentionParser.findActiveMention('请@deep', 6);
      expect(match, isNotNull);
      expect(match!.start, 1);
      expect(match.end, 6);
    });

    test('光标在中间位置时识别前一个词', () {
      final match = AtMentionParser.findActiveMention('请@deepseek 继续', 6);
      expect(match, isNotNull);
      expect(match!.start, 1);
      expect(match.end, 6);
    });

    test('mention 后有空格不再激活', () {
      final match = AtMentionParser.findActiveMention('@deepseek 继续', 10);
      expect(match, isNull);
    });

    test('无 @ 时返回 null', () {
      expect(AtMentionParser.findActiveMention('hello world', 11), isNull);
    });

    test('空文本返回 null', () {
      expect(AtMentionParser.findActiveMention('', 0), isNull);
    });

    test('@ 后跟非名字字符不激活', () {
      final match = AtMentionParser.findActiveMention('请@！', 3);
      expect(match, isNull);
    });
  });

  group('AtMentionParser.applySuggestion', () {
    test('替换正在输入的 mention', () {
      final text = AtMentionParser.applySuggestion('请@deep 回答', 6, 'deepseek');
      expect(text, '请@deepseek 回答');
    });

    test('未激活时不修改', () {
      final text = AtMentionParser.applySuggestion('请回答问题', 6, 'deepseek');
      expect(text, '请回答问题');
    });
  });
}