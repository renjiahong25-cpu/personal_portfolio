// penpot-export-collector.mjs — 限时本地接收器（配套 Penpot 插件端 fetch 导出）
// 用法：node diagnostics/penpot-export-collector.mjs <输出目录>
// 行为：监听 127.0.0.1:19399，POST /save {page,name,type,content} → 写盘 <输出目录>/<page>/<name>.<type>
//       POST /done → 写盘计数后关闭；240 秒内没 /done 自动退出（code 2）。绝不留驻。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const PORT = 19399;
const OUT = process.argv[2] || 'docs/prototype-export';
fs.mkdirSync(OUT, { recursive: true });
let saved = 0;
const sanitize = (s) => String(s).replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_');

const server = http.createServer((req, res) => {
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
  };
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end(); }
  if (req.url === '/ping') {
    res.writeHead(200, { 'Content-Type': 'application/json', ...cors });
    return res.end(JSON.stringify({ ok: true, saved }));
  }
  if (req.method === 'POST' && (req.url === '/save' || req.url === '/done')) {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      if (req.url === '/done') {
        res.writeHead(200, { 'Content-Type': 'application/json', ...cors });
        res.end(JSON.stringify({ ok: true, saved, out: OUT }));
        console.log('DONE saved=' + saved);
        server.close();
        process.exit(0);
        return;
      }
      try {
        const d = JSON.parse(body);
        const dir = path.join(OUT, sanitize(d.page));
        fs.mkdirSync(dir, { recursive: true });
        const file = path.join(dir, sanitize(d.name) + '.' + (d.type || 'svg'));
        fs.writeFileSync(file, d.content, 'utf8');
        saved++;
        console.log('SAVED ' + d.page + '/' + d.name + '.' + (d.type || 'svg') + ' (' + d.content.length + ' chars)');
        res.writeHead(200, { 'Content-Type': 'application/json', ...cors });
        res.end(JSON.stringify({ ok: true, saved }));
      } catch (e) {
        console.log('ERR ' + e.message);
        res.writeHead(500, { 'Content-Type': 'application/json', ...cors });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
    });
    return;
  }
  res.writeHead(404, cors);
  res.end('not found');
});

server.listen(PORT, '127.0.0.1', () => console.log('LISTENING 127.0.0.1:' + PORT + ' out=' + OUT));
setTimeout(() => { console.log('TIMEOUT exiting (no /done within 240s)'); process.exit(2); }, 240000);
