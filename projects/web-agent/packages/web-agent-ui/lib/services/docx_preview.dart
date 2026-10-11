import 'dart:convert';
import 'dart:io';

import 'package:archive/archive_io.dart';

/// 从 .docx 文件提取纯文本（解 word/document.xml 去标签）。
///
/// 非 docx / 结构损坏时抛出异常，调用方据此回退到系统打开。
Future<String> extractDocxText(File file) async {
  if (!file.existsSync()) {
    throw Exception('文件不存在：${file.path}');
  }
  final bytes = file.readAsBytesSync();
  final archive = ZipDecoder().decodeBytes(bytes);
  final document = archive.findFile('word/document.xml');
  if (document == null) {
    throw const FormatException('docx 缺少 word/document.xml');
  }
  final xml = utf8.decode(document.content as List<int>);
  final text = xml
      .replaceAll(RegExp(r'<w:tab[ >]'), '\t')
      .replaceAll(RegExp(r'<w:br[ /]'), '\n')
      .replaceAll(RegExp(r'<w:p[ >]'), '\n')
      .replaceAll(RegExp(r'<w:tr[ >]'), '\n')
      .replaceAll(RegExp(r'<[^>]+>'), '')
      .replaceAll('&amp;', '&')
      .replaceAll('&lt;', '<')
      .replaceAll('&gt;', '>')
      .replaceAll('&quot;', '"')
      .replaceAll('&#39;', "'")
      .replaceAll(RegExp(r'[ \t]+\n'), '\n')
      .replaceAll(RegExp(r'\n{3,}'), '\n\n')
      .trim();
  if (text.isEmpty) {
    throw const FormatException('docx 未提取到文本');
  }
  return text;
}