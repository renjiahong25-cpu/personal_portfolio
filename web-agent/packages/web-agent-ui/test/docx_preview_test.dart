import 'dart:convert';
import 'dart:io';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:web_agent_ui/services/docx_preview.dart';

const _tempRoot = 'build/test_tmp';

void main() {
  setUp(() {
    if (!Directory(_tempRoot).existsSync()) {
      Directory(_tempRoot).createSync(recursive: true);
    }
  });

  test('提取 docx 文本（段落与常见转义）', () async {
    final file = File('$_tempRoot/sample.docx');
    final archive = Archive()
      ..add(
        ArchiveFile.bytes(
          'word/document.xml',
          utf8.encode(
            '<w:document><w:body>'
            '<w:p><w:r><w:t>Hello &amp; welcome</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>第二段</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>单元格 &lt;x&gt;</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>',
          ),
        ),
      );
    final bytes = ZipEncoder().encode(archive);
    file.writeAsBytesSync(bytes);

    final text = await extractDocxText(file);
    expect(text, contains('Hello & welcome'));
    expect(text, contains('第二段'));
    expect(text, contains('单元格 <x>'));
    expect(text, contains('\n'));
  });

  test('非 zip 或缺少 document.xml 抛异常', () async {
    final notDocx = File('$_tempRoot/bad.docx');
    notDocx.writeAsStringSync('not a zip');

    final missingXml = File('$_tempRoot/missing.docx');
    final archive = Archive()
      ..add(ArchiveFile.string('word/style.xml', '<x/>'));
    final zipBytes = ZipEncoder().encode(archive);
    missingXml.writeAsBytesSync(zipBytes);

    await expectLater(extractDocxText(notDocx), throwsA(anything));
    await expectLater(extractDocxText(missingXml), throwsA(anything));
  });
}