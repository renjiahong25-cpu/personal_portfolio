"""Normal QR login. Same-origin browsers or loopback JSON CLI requests only.

Origin-free CLI calls are intentionally supported; both paths require the
Host to name our actual loopback listener, not a name supplied by another site.
"""

from urllib.parse import urlsplit

from core.errors import ValidationError

from .base import DEV_ORIGINS, BaseHandler, api_endpoint


class KugouAuthHandler(BaseHandler):
    source = "kugou"
    @api_endpoint
    async def post(self, action: str) -> None:
        # JSON + origin checks protect the local account flow from another site.
        stream = getattr(self.request.connection, "stream", None)
        socket = getattr(stream, "socket", None)
        actual_port = socket.getsockname()[1] if socket else self.services.settings.port
        try:
            host = urlsplit(f"http://{self.request.host}")
            valid_host = (host.hostname in {"localhost", "127.0.0.1", "::1"}
                          and (host.port or 80) == actual_port
                          and not host.username and not host.password)
        except ValueError:
            valid_host = False
        if not valid_host:
            raise ValidationError("login host must match the loopback listener")
        origin = self.request.headers.get("Origin", "")
        own_origin = f"{self.request.protocol}://{self.request.host}"
        if origin and origin not in (*DEV_ORIGINS, own_origin):
            raise ValidationError("login origin is not allowed")
        if "application/json" not in self.request.headers.get("Content-Type", ""):
            raise ValidationError("login requires application/json")
        extractor = self.services.extractors.get(self.source)
        self.set_header("Cache-Control", "no-store")
        if action == "create":
            self.write_json(await extractor.create_login_qr())
        else:
            session = self.require_str(self.json_body(), "sessionId")
            self.write_json(await extractor.check_login_qr(session))


class NeteaseAuthHandler(KugouAuthHandler):
    source = "netease"


class KugouLoginPageHandler(BaseHandler):
    async def get(self, source: str = "kugou") -> None:
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.set_header("Cache-Control", "no-store")
        page = LOGIN_PAGE if source == "kugou" else LOGIN_PAGE.replace("酷狗", "网易云").replace("/auth/kugou/", "/auth/netease/")
        self.finish(page)


LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PianoFlow 酷狗登录</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font:16px system-ui;background:#071520;color:#e5f1f7;margin:0;display:grid;place-items:center;min-height:100vh}
main{width:min(430px,88vw);padding:32px;background:#122633;border-radius:20px;text-align:center}
img{width:240px;height:240px;background:white;padding:12px;border-radius:12px}button,a{color:#82dec6}button{background:#214539;border:0;border-radius:9px;padding:12px 24px;cursor:pointer}p{line-height:1.6}
</style><main><h1>登录酷狗音乐</h1><p>使用酷狗 App 扫码并确认登录。PianoFlow 自动保存本次登录，供搜索与播放使用。</p>
<img id="qr" hidden alt="酷狗官方登录二维码"><p id="status">正在获取二维码…</p><button id="refresh">重新获取二维码</button>
<p><a href="/">回到 PianoFlow</a></p></main><script>
let generation=0;
async function call(action,body={}){let r=await fetch('/api/music/auth/kugou/qr/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let p=await r.json();if(!r.ok)throw Error(p.error?.message||'登录服务暂时不可用');return p}
async function start(){let own=++generation;document.getElementById('qr').hidden=true;document.getElementById('status').textContent='正在获取二维码…';try{let p=await call('create');if(own!==generation)return;document.getElementById('qr').src=p.qrImage;document.getElementById('qr').hidden=false;document.getElementById('status').textContent='请用酷狗 App 扫码';let deadline=Date.now()+p.expiresIn*1000;while(own===generation&&Date.now()<deadline){await new Promise(r=>setTimeout(r,2500));if(own!==generation)return;let s=await call('check',{sessionId:p.sessionId});if(own!==generation)return;document.getElementById('status').textContent={waiting:'等待扫码',confirming:'请在手机上确认登录',authenticated:'登录成功，账号已连接。可以返回 PianoFlow 搜索。',expired:'二维码已过期，请重新获取'}[s.status]||'等待扫码';if(s.status==='authenticated'||s.status==='expired')return}if(own===generation)document.getElementById('status').textContent='二维码已过期，请重新获取'}catch(e){if(own===generation)document.getElementById('status').textContent=e.message}}
document.getElementById('refresh').onclick=start;start();
</script></html>"""
