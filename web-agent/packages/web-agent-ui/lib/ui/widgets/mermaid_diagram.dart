import 'dart:math' as math;

import 'package:flutter/material.dart';

/// 从回复文本中提取 ```mermaid ... ``` 代码块；无围栏则返回 null。
String? extractMermaidCodeBlock(String content) {
  final fence = RegExp(r'```mermaid\s*\n([\s\S]*?)```');
  final match = fence.firstMatch(content);
  if (match != null) return match.group(1)?.trim();
  if (RegExp(r'^(graph|flowchart)\s+(TD|TB|LR)\b').hasMatch(content.trim())) {
    return content.trim();
  }
  return null;
}

enum MermaidDirection { topDown, leftRight }

enum MermaidNodeShape { rect, stadium, diamond }

class MermaidNode {
  MermaidNode({
    required this.id,
    required this.label,
    this.shape = MermaidNodeShape.rect,
  });

  final String id;
  String label;
  MermaidNodeShape shape;

  late Offset position;
  Size size = Size.zero;
}

class MermaidEdge {
  MermaidEdge({
    required this.from,
    required this.to,
    this.label,
    this.dashed = false,
    this.thick = false,
    this.arrow = true,
  });

  final String from;
  final String to;
  final String? label;
  final bool dashed;
  final bool thick;
  final bool arrow;
}

class MermaidGraph {
  MermaidGraph({
    required this.direction,
    required this.nodes,
    required this.edges,
    this.subgraphTitles = const {},
  });

  final MermaidDirection direction;
  final List<MermaidNode> nodes;
  final List<MermaidEdge> edges;
  final Map<String, String> subgraphTitles;
}

class MermaidParseException implements Exception {
  MermaidParseException(this.message);

  final String message;

  @override
  String toString() => 'MermaidParseException: $message';
}

/// 解析受控子集：graph/flowchart (TD|LR)、节点 `A` `A[label]` `A((label))` `A{label}`、
/// 边 `-->` `---` `-.->` `==>`、边标签 `A -- label --> B` / `A -->|label| B`、
/// `subgraph 标题 ... end`（外层描框）。
MermaidGraph parseMermaid(String source) {
  final lines = source
      .split('\n')
      .map((line) => line.trim())
      .where((line) => line.isNotEmpty)
      .toList();
  if (lines.isEmpty) throw MermaidParseException('空内容');
  final header = lines.first.toLowerCase();
  final direction = header.contains('lr')
      ? MermaidDirection.leftRight
      : MermaidDirection.topDown;

  final nodes = <String, MermaidNode>{};
  final edges = <MermaidEdge>[];
  final subgraphTitles = <String, String>{};
  var currentSubgraph = '';

  MermaidNode ensure(String id) => nodes.putIfAbsent(id, () => MermaidNode(id: id, label: id));

  for (final line in lines.skip(1)) {
    if (line.startsWith('subgraph')) {
      currentSubgraph = line.substring('subgraph'.length).trim();
      continue;
    }
    if (line == 'end') {
      currentSubgraph = '';
      continue;
    }
    final edge = _parseEdge(line);
    if (edge != null) {
      final fromNode = ensure(edge.from);
      final toNode = ensure(edge.to);
      if (currentSubgraph.isNotEmpty) {
        subgraphTitles[fromNode.id] = currentSubgraph;
        subgraphTitles[toNode.id] = currentSubgraph;
      }
      edges.add(edge);
      continue;
    }
    final spec = _parseNode(line);
    if (spec != null) {
      final node = ensure(spec.id);
      node.label = spec.label;
      node.shape = spec.shape;
      if (currentSubgraph.isNotEmpty) subgraphTitles[node.id] = currentSubgraph;
      continue;
    }
  }

  if (nodes.isEmpty) throw MermaidParseException('未解析到节点');

  return MermaidGraph(
    direction: direction,
    nodes: nodes.values.toList(),
    edges: edges,
    subgraphTitles: subgraphTitles,
  );
}

typedef _NodeSpec = ({String id, String label, MermaidNodeShape shape});

_NodeSpec? _parseNode(String line) {
  final rect = RegExp(r'^([\w.\-/]+)\s*\[(.+)\]$').firstMatch(line);
  if (rect != null) {
    return (id: rect.group(1)!, label: rect.group(2)!, shape: MermaidNodeShape.rect);
  }
  final stadium = RegExp(r'^([\w.\-/]+)\s*\(\((.+)\)\)$').firstMatch(line);
  if (stadium != null) {
    return (id: stadium.group(1)!, label: stadium.group(2)!, shape: MermaidNodeShape.stadium);
  }
  final diamond = RegExp(r'^([\w.\-/]+)\s*\{(.+)\}$').firstMatch(line);
  if (diamond != null) {
    return (id: diamond.group(1)!, label: diamond.group(2)!, shape: MermaidNodeShape.diamond);
  }
  final bare = RegExp(r'^([\w.\-/]+)$').firstMatch(line);
  if (bare != null) {
    return (id: bare.group(1)!, label: bare.group(1)!, shape: MermaidNodeShape.rect);
  }
  return null;
}

MermaidEdge? _parseEdge(String line) {
  const ops = ['-.->', '==>', '-->', '---'];
  String? op;
  var index = -1;
  for (final candidate in ops) {
    final i = line.indexOf(candidate);
    if (i >= 0 && (index < 0 || i < index)) {
      op = candidate;
      index = i;
    }
  }
  if (op == null) return null;

  var rawLeft = line.substring(0, index).trim();
  var rawRight = line.substring(index + op.length).trim();

  String? label;
  if (rawRight.startsWith('|')) {
    final end = rawRight.indexOf('|', 1);
    if (end > 0) {
      label = rawRight.substring(1, end).trim();
      rawRight = rawRight.substring(end + 1).trim();
    }
  }
  final leftLabel = rawLeft.split(RegExp(r'\s--\s'));
  var from = _bareId(rawLeft);
  if (leftLabel.length == 2 && op == '-->') {
    from = _bareId(leftLabel[0]);
    label ??= leftLabel[1].trim();
  }

  final to = _bareId(rawRight.split(RegExp(r'\s+')).first);
  if (from.isEmpty || to.isEmpty) return null;
  return MermaidEdge(
    from: from,
    to: to,
    label: label,
    dashed: op == '-.->',
    thick: op == '==>',
    arrow: op != '---',
  );
}

/// 取出节点 id：`A[入口]` → `A`、`B((服务))` → `B`、`C{决策}` → `C`。
String _bareId(String raw) {
  final match = RegExp(r'^[^\s\[({]+').firstMatch(raw.trim());
  return match?.group(0) ?? raw.trim();
}

class MermaidPalette {
  const MermaidPalette({
    required this.nodeFill,
    required this.nodeStroke,
    required this.nodeText,
    required this.edgeColor,
    required this.subgraphFill,
  });

  factory MermaidPalette.of(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return MermaidPalette(
      nodeFill: scheme.surfaceContainerHighest,
      nodeStroke: scheme.outline,
      nodeText: scheme.onSurface,
      edgeColor: scheme.outline,
      subgraphFill: scheme.surfaceContainerLow.withValues(alpha: 0.4),
    );
  }

  final Color nodeFill;
  final Color nodeStroke;
  final Color nodeText;
  final Color edgeColor;
  final Color subgraphFill;
}

/// 布局：测量文本 → 依赖分层 → 分配坐标。返回 (图，整体尺寸)。
(MermaidGraph, Size) layoutMermaid(
  MermaidGraph graph, {
  double nodeMinWidth = 96,
  double nodeMinHeight = 40,
  double gapAxis = 64,
  double gapCross = 24,
  double padding = 24,
}) {
  const nodeFont = TextStyle(fontSize: 12, fontFamily: 'monospace');

  Size textSize(String text) {
    final tp = TextPainter(
      textDirection: TextDirection.ltr,
      text: TextSpan(text: text, style: nodeFont),
    )..layout(maxWidth: 160);
    return tp.size;
  }

  for (final node in graph.nodes) {
    final t = textSize(node.label);
    node.size = Size(
      math.max(nodeMinWidth, t.width + 24),
      math.max(nodeMinHeight, t.height + 18),
    );
  }

  final rankOf = <String, int>{};
  for (final node in graph.nodes) {
    rankOf[node.id] = 0;
  }
  var changed = true;
  var guard = 0;
  while (changed && guard < graph.nodes.length + 4) {
    changed = false;
    guard++;
    for (final edge in graph.edges) {
      final fromRank = rankOf[edge.from];
      if (fromRank == null) continue;
      if ((rankOf[edge.to] ?? 0) < fromRank + 1) {
        rankOf[edge.to] = fromRank + 1;
        changed = true;
      }
    }
  }

  final byRank = <int, List<MermaidNode>>{};
  for (final node in graph.nodes) {
    byRank.putIfAbsent(rankOf[node.id] ?? 0, () => []).add(node);
  }
  final ranks = byRank.keys.toList()..sort();

  // 主轴（层方向）跨度
  final rowOrCol = graph.direction == MermaidDirection.topDown
      ? (List<MermaidNode> nodes) => _maxWidth(nodes, gapCross)
      : (List<MermaidNode> nodes) => _maxHeight(nodes, gapCross);

  var maxCross = 0.0;
  for (final rank in ranks) {
    maxCross = math.max(maxCross, rowOrCol(byRank[rank]!));
  }
  final axisStep = nodeStep(graph.direction, nodeMinHeight, nodeMinWidth, gapAxis);
  final axisLength = axisStep * (ranks.length - 1) + nodeAxisExtent(graph.direction, nodeMinHeight, nodeMinWidth);

  // 分配坐标：层内跨轴居中堆叠
  for (final rank in ranks) {
    final nodes = byRank[rank]!;
    final rankAxis = rank * axisStep;
    var cursor = 0.0;
    for (final node in nodes) {
      final half = graph.direction == MermaidDirection.topDown
          ? node.size.width / 2
          : node.size.height / 2;
      final center = cursor + half;
      cursor += (graph.direction == MermaidDirection.topDown ? node.size.width : node.size.height) + gapCross;
      node.position = graph.direction == MermaidDirection.topDown
          ? Offset(center, rankAxis)
          : Offset(rankAxis, center);
    }
  }

  return (
    graph,
    Size(
      graph.direction == MermaidDirection.topDown ? maxCross : axisLength,
      graph.direction == MermaidDirection.topDown ? axisLength : maxCross,
    ),
  );
}

double _maxWidth(List<MermaidNode> nodes, double gapCross) {
  var w = 0.0;
  for (final node in nodes) {
    w += node.size.width;
  }
  return w + gapCross * (nodes.length - 1);
}

double _maxHeight(List<MermaidNode> nodes, double gapCross) {
  var h = 0.0;
  for (final node in nodes) {
    h += node.size.height;
  }
  return h + gapCross * (nodes.length - 1);
}

double nodeStep(MermaidDirection direction, double nodeMinHeight, double nodeMinWidth, double gapAxis) =>
    (direction == MermaidDirection.topDown ? nodeMinHeight : nodeMinWidth) + gapAxis;

double nodeAxisExtent(MermaidDirection direction, double nodeMinHeight, double nodeMinWidth) =>
    direction == MermaidDirection.topDown ? nodeMinHeight : nodeMinWidth;

/// 架构图组件：解析失败时回退为等宽文本（内容不丢）。
class MermaidDiagram extends StatelessWidget {
  const MermaidDiagram({super.key, required this.source});

  final String source;

  @override
  Widget build(BuildContext context) {
    final palette = MermaidPalette.of(context);
    MermaidGraph graph;
    try {
      graph = parseMermaid(source);
    } on MermaidParseException {
      return Container(
        key: const ValueKey('mermaid-fallback'),
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(6),
        ),
        child: SelectableText(source, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
      );
    }
    final (_, size) = layoutMermaid(graph);
    return SizedBox(
      width: size.width,
      height: size.height,
      child: CustomPaint(
        painter: _MermaidPainter(graph: graph, palette: palette, size: size),
      ),
    );
  }
}

class _MermaidPainter extends CustomPainter {
  _MermaidPainter({required this.graph, required this.palette, required this.size});

  final MermaidGraph graph;
  final MermaidPalette palette;
  final Size size;

  @override
  void paint(Canvas canvas, Size size) {
    // subgraph 外层描框
    final groups = <String, Rect>{};
    for (final node in graph.nodes) {
      final title = graph.subgraphTitles[node.id];
      if (title == null || title.isEmpty) continue;
      final rect = _nodeRect(node);
      final existing = groups[title];
      groups[title] = existing == null
          ? rect
          : Rect.fromLTRB(
              math.min(existing.left, rect.left) - 6,
              math.min(existing.top, rect.top) - 6,
              math.max(existing.right, rect.right) + 6,
              math.max(existing.bottom, rect.bottom) + 6,
            );
    }
    final groupPaint = Paint()..color = palette.subgraphFill;
    for (final entry in groups.entries) {
      final r = RRect.fromRectAndRadius(entry.value, const Radius.circular(8));
      canvas.drawRRect(r, groupPaint);
      final tp = TextPainter(
        textDirection: TextDirection.ltr,
        text: TextSpan(text: entry.key, style: const TextStyle(fontSize: 10)),
      )..layout();
      tp.paint(canvas, Offset(entry.value.left + 8, entry.value.top + 2));
    }

    // 边
    for (final edge in graph.edges) {
      final from = graph.nodes.where((n) => n.id == edge.from).firstOrNull;
      final to = graph.nodes.where((n) => n.id == edge.to).firstOrNull;
      if (from == null || to == null) continue;
      _drawEdge(canvas, from, to, edge);
    }

    // 节点
    for (final node in graph.nodes) {
      _drawNode(canvas, node);
    }
  }

  Rect _nodeRect(MermaidNode node) {
    return Rect.fromCenter(
      center: node.position,
      width: node.size.width,
      height: node.size.height,
    );
  }

  void _drawNode(Canvas canvas, MermaidNode node) {
    final fill = Paint()..color = palette.nodeFill;
    final stroke = Paint()
      ..color = palette.nodeStroke
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2;
    final rect = _nodeRect(node);
    switch (node.shape) {
      case MermaidNodeShape.stadium:
        canvas.drawOval(rect, fill);
        canvas.drawOval(rect, stroke);
      case MermaidNodeShape.diamond:
        final path = Path()
          ..moveTo(rect.center.dx, rect.top)
          ..lineTo(rect.right, rect.center.dy)
          ..lineTo(rect.center.dx, rect.bottom)
          ..lineTo(rect.left, rect.center.dy)
          ..close();
        canvas.drawPath(path, fill);
        canvas.drawPath(path, stroke);
      case MermaidNodeShape.rect:
        final r = RRect.fromRectAndRadius(rect, const Radius.circular(6));
        canvas.drawRRect(r, fill);
        canvas.drawRRect(r, stroke);
    }
    final tp = TextPainter(
      textDirection: TextDirection.ltr,
      text: TextSpan(text: node.label, style: TextStyle(fontSize: 12, fontFamily: 'monospace', color: palette.nodeText)),
    )..layout(maxWidth: rect.width - 16);
    tp.paint(canvas, Offset(rect.center.dx - tp.width / 2, rect.center.dy - tp.height / 2));
  }

  void _drawEdge(Canvas canvas, MermaidNode from, MermaidNode to, MermaidEdge edge) {
    final paint = Paint()
      ..color = palette.edgeColor
      ..strokeWidth = (edge.thick ? 2.2 : 1.4)
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;
    final a = _boundaryPoint(from, to);
    final b = _boundaryPoint(to, from);
    if (edge.dashed && !edge.thick) {
      canvas.drawPath(_dashedPath(a, b), paint);
    } else if (edge.thick) {
      canvas.drawLine(a, b, paint);
      canvas.drawLine(a.translate(0, 3), b.translate(0, 3), paint);
    } else {
      canvas.drawLine(a, b, paint);
    }
    if (edge.arrow) {
      _drawArrowHead(canvas, b, (a - b).direction);
    }
    if (edge.label != null) {
      final mid = Offset((a.dx + b.dx) / 2, (a.dy + b.dy) / 2);
      final tp = TextPainter(
        textDirection: TextDirection.ltr,
        text: TextSpan(text: edge.label, style: TextStyle(fontSize: 10, color: palette.edgeColor)),
      )..layout();
      tp.paint(canvas, mid - Offset(tp.width / 2, tp.height / 2));
    }
  }

  Path _dashedPath(Offset a, Offset b) {
    final path = Path();
    final total = (b - a).distance;
    if (total < 1) return path..moveTo(a.dx, a.dy);
    final dir = (b - a) / total;
    const dash = 6.0;
    const gap = 4.0;
    var traveled = 0.0;
    path.moveTo(a.dx, a.dy);
    var on = true;
    while (traveled < total) {
      final step = math.min((on ? dash : gap), total - traveled);
      final point = a + dir * (traveled + step);
      if (on) {
        path.lineTo(point.dx, point.dy);
      } else {
        path.moveTo(point.dx, point.dy);
      }
      traveled += step;
      on = !on;
    }
    return path;
  }

  void _drawArrowHead(Canvas canvas, Offset tip, double angle) {
    final path = Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(tip.dx - 8 * math.cos(angle - 0.42), tip.dy - 8 * math.sin(angle - 0.42))
      ..lineTo(tip.dx - 8 * math.cos(angle + 0.42), tip.dy - 8 * math.sin(angle + 0.42))
      ..close();
    canvas.drawPath(path, Paint()..color = palette.edgeColor);
  }

  Offset _boundaryPoint(MermaidNode source, MermaidNode target) {
    final rect = _nodeRect(source);
    final center = source.position;
    final delta = target.position - center;
    if (delta == Offset.zero) return center;
    final angle = delta.direction;
    // 逐象限求到矩形边界
    double t;
    if (source.shape == MermaidNodeShape.stadium) {
      return Offset(center.dx + rect.width / 2 * math.cos(angle), center.dy + rect.height / 2 * math.sin(angle));
    }
    if (source.shape == MermaidNodeShape.diamond) {
      final cosA = math.cos(angle).abs();
      final sinA = math.sin(angle).abs();
      t = (sinA / rect.height) + (cosA / rect.width) > 0 ? 1 / ((sinA / rect.height) + (cosA / rect.width)) : 1;
      return Offset(center.dx + t * math.cos(angle), center.dy + t * math.sin(angle));
    }
    final halfW = rect.width / 2;
    final halfH = rect.height / 2;
    final tx = halfW / math.cos(angle).abs().clamp(0.01, 1.0);
    final ty = halfH / math.sin(angle).abs().clamp(0.01, 1.0);
    t = math.min(tx, ty);
    return Offset(center.dx + t * math.cos(angle), center.dy + t * math.sin(angle));
  }

  @override
  bool shouldRepaint(_MermaidPainter oldDelegate) =>
      oldDelegate.graph != graph || oldDelegate.palette != palette;
}