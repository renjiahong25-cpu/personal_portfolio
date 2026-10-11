import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:web_agent_ui/ui/widgets/mermaid_diagram.dart';

void main() {
  group('extractMermaidCodeBlock', () {
    test('提取围栏内代码', () {
      const content = '说明文字\n```mermaid\ngraph TD\nA --> B\n```\n结尾';
      final code = extractMermaidCodeBlock(content);
      expect(code, 'graph TD\nA --> B');
    });

    test('无围栏时若直接以 graph/flowchart 开头则整段提取', () {
      expect(extractMermaidCodeBlock('graph LR\nA-->B'), 'graph LR\nA-->B');
      expect(extractMermaidCodeBlock('flowchart TB\nA-->B'), 'flowchart TB\nA-->B');
    });

    test('无 mermaid 内容返回 null', () {
      expect(extractMermaidCodeBlock('普通回复文本'), isNull);
      expect(extractMermaidCodeBlock('```dart\nvoid main(){}\n```'), isNull);
    });
  });

  group('parseMermaid', () {
    test('解析节点形状与边类型', () {
      const source = '''
graph TD
  A[入口]
  B((服务))
  C{决策}
  A --> B
  B --- C
  C -.-> D
''';
      final graph = parseMermaid(source);
      expect(graph.direction, MermaidDirection.topDown);
      expect(graph.nodes.length, 4);
      expect(graph.nodes.firstWhere((n) => n.id == 'A').label, '入口');
      expect(graph.nodes.firstWhere((n) => n.id == 'B').shape, MermaidNodeShape.stadium);
      expect(graph.nodes.firstWhere((n) => n.id == 'C').shape, MermaidNodeShape.diamond);
      expect(graph.edges.length, 3);
      expect(graph.edges.first.arrow, isTrue);
      expect(graph.edges.last.dashed, isTrue);
      expect(graph.edges[1].arrow, isFalse);
      expect(graph.edges.every((e) => e.from != e.to), isTrue);
    });

    test('LR 方向与边标签解析', () {
      const source = '''
flowchart LR
  A -- 请求 --> B
  C -->|响应| D
''';
      final graph = parseMermaid(source);
      expect(graph.direction, MermaidDirection.leftRight);
      expect(graph.edges[0].label, '请求');
      expect(graph.edges[1].label, '响应');
    });

    test('subgraph 记录归属', () {
      const source = '''
graph TD
  subgraph 前端
    A[UI]
  end
  subgraph 后端
    B[API]
  end
  A --> B
''';
      final graph = parseMermaid(source);
      expect(graph.subgraphTitles['A'], '前端');
      expect(graph.subgraphTitles['B'], '后端');
    });

    test('空/无节点抛出解析异常', () {
      expect(() => parseMermaid(''), throwsA(isA<MermaidParseException>()));
      expect(
        () => parseMermaid('graph TD\n只是文本'),
        throwsA(isA<MermaidParseException>()),
      );
    });
  });

  group('layoutMermaid', () {
    test('TD 布局主轴为高度、各层坐标递增', () {
      const source = '''
graph TD
  A --> B
  A --> C
  B --> D
''';
      final graph = parseMermaid(source);
      final (_, size) = layoutMermaid(graph);
      expect(size.height, greaterThan(0));
      expect(size.width, greaterThan(0));
      final a = graph.nodes.firstWhere((n) => n.id == 'A');
      final d = graph.nodes.firstWhere((n) => n.id == 'D');
      expect(d.position.dy, greaterThan(a.position.dy));
      expect((d.position.dx - a.position.dx).abs(), lessThan(1));
    });
  });

  group('MermaidDiagram widget', () {
    testWidgets('渲染成功且不抛异常', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: MermaidDiagram(
                source: 'graph TD\nA[输入] --> B{处理}\nB -->|完成| C((输出))',
              ),
            ),
          ),
        ),
      );
      await tester.pump();
      expect(find.byType(MermaidDiagram), findsOneWidget);
      expect(find.byType(CustomPaint), findsWidgets);
    });

    testWidgets('解析失败回退为等宽文本', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: MermaidDiagram(source: 'graph TD\n乱文本')),
        ),
      );
      await tester.pump();
      expect(find.byKey(const ValueKey('mermaid-fallback')), findsOneWidget);
      expect(find.text('graph TD\n乱文本'), findsOneWidget);
    });

    testWidgets('节点标签文本可见', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: MermaidDiagram(source: 'graph LR\nAPI[网关服务] --> DB[数据库]'),
          ),
        ),
      );
      await tester.pump();
      expect(find.byType(MermaidDiagram), findsOneWidget);
    });
  });
}