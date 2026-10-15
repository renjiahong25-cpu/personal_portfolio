"""User-authorized public-guide fetch and deterministic readable-text extraction.

Network connections are pinned to a validated public address; no cookies, proxy,
private DNS targets, scripts, browser execution or credential-bearing URL support.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import hashlib, http.client, ipaddress, re, socket, ssl, time, threading
from urllib.parse import urlsplit, urlunsplit, urljoin

MAX_BYTES=2*1024*1024
MAX_TEXT=64000
MAX_REDIRECTS=3
TOTAL_TIMEOUT=15
class GuideImportError(ValueError): pass
DNS_SLOTS=threading.BoundedSemaphore(4)

def resolve_public_addresses(host,port):
    if not DNS_SLOTS.acquire(blocking=False): raise GuideImportError('公网DNS查询繁忙，请稍后重试')
    result=[]; error=[]; done=threading.Event()
    def resolve():
        try: result.extend(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
        except OSError: error.append(True)
        finally: DNS_SLOTS.release(); done.set()
    threading.Thread(target=resolve,daemon=True).start()
    if not done.wait(6): raise GuideImportError('公网DNS查询超时')
    if error: raise OSError()
    return result

@dataclass(frozen=True)
class PublicTarget:
    url: str
    host: str
    port: int
    address: str
    secure: bool
    path: str

@dataclass(frozen=True)
class GuideDocument:
    url: str
    title: str
    text: str
    fetched_at: float
    sha256: str
    truncated: bool

def public_target(url):
    if not isinstance(url,str) or len(url)>1500 or any(ord(c)<32 for c in url):
        raise GuideImportError('网址格式无效或过长')
    try:
        parsed=urlsplit(url.strip())
        host=(parsed.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        secure=parsed.scheme=='https'; port=parsed.port or (443 if secure else 80)
    except (ValueError,UnicodeError): raise GuideImportError('网址格式无效') from None
    if parsed.scheme not in ('http','https') or not host or parsed.username is not None or parsed.password is not None:
        raise GuideImportError('仅接受不含账号密码的公开 HTTP(S) 攻略网址')
    if port!=(443 if secure else 80): raise GuideImportError('仅允许默认 HTTP/HTTPS 端口')
    if host in ('localhost','localhost.localdomain') or host.endswith(('.localhost','.local','.internal','.home')) or '%' in host:
        raise GuideImportError('不允许本机或私网网址')
    try:
        answers=resolve_public_addresses(host,port)
        addresses=list(dict.fromkeys(x[4][0] for x in answers))
        if not addresses: raise OSError()
        for address in addresses:
            ip=ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip,'ipv4_mapped',None):
                raise GuideImportError('网址解析包含非公网地址，已拒绝')
    except GuideImportError: raise
    except (OSError,ValueError): raise GuideImportError('无法确认网址的公网地址') from None
    host_header='['+host+']' if ':' in host else host
    normalized=urlunsplit((parsed.scheme,host_header,parsed.path or '/',parsed.query,''))
    path=urlunsplit(('','',parsed.path or '/',parsed.query,''))
    return PublicTarget(normalized,host,port,addresses[0],secure,path)

class PinnedConnection(http.client.HTTPConnection):
    def __init__(self,target,timeout):
        super().__init__(target.host,target.port,timeout=timeout)
        self.target=target
    def connect(self):
        self.sock=socket.create_connection((self.target.address,self.target.port),self.timeout)
        if self.target.secure:
            self.sock=ssl.create_default_context().wrap_socket(self.sock,server_hostname=self.target.host)

class ReadableHTML(HTMLParser):
    skip_tags={'script','style','noscript','svg','nav','footer','header','form','button','iframe','template','aside','textarea','select'}
    breaks={'p','br','div','li','article','section','main','h1','h2','h3','h4','h5','h6','tr','dt','dd'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.fragments=[]; self.title_fragments=[]; self.in_title=False
    def handle_starttag(self,tag,attrs):
        tag=tag.lower()
        if tag=='title': self.in_title=True
        attributes=dict(attrs)
        hidden='hidden' in attributes or attributes.get('aria-hidden','').lower()=='true' or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)',attributes.get('style',''),re.I))
        if tag in self.skip_tags or self.stack or hidden:
            # Void tags inside hidden areas do not affect stack balance.
            if tag not in ('br','img','input','meta','link','hr','source','wbr','area','base','embed','param'): self.stack.append(tag)
            return
        if tag in self.breaks: self.fragments.append('\n')
    def handle_endtag(self,tag):
        if tag=='title': self.in_title=False
        if self.stack:
            if tag in self.stack:
                while self.stack:
                    opened=self.stack.pop()
                    if opened==tag: break
            return
        if tag in self.breaks: self.fragments.append('\n')
    def handle_data(self,data):
        if self.in_title: self.title_fragments.append(data)
        elif not self.stack: self.fragments.append(data)
    def readable(self):
        title=re.sub(r'\s+',' ',''.join(self.title_fragments)).strip()[:240]
        text='\n'.join(re.sub(r'[\t \r\f\v]+',' ',line).strip() for line in ''.join(self.fragments).splitlines())
        text=re.sub(r'\n{3,}','\n\n',text).strip()
        recommendations=re.search(r'(?:^|\n)(?:猜你喜欢|相关推荐|猜你想搜|更多推荐)\s*(?:\n|$)',text)
        if recommendations: text=text[:recommendations.start()].strip()
        return title,text

class TapTapCurrentPost(HTMLParser):
    """Only the current post's rich-content wrapper inside the primary detail area.

    Do not fall back to page text or recommendation cards when the article body
    is short/image-only, a challenge page, or the site's markup has changed.
    """
    void_tags={'br','img','input','meta','link','hr','source','wbr','area','base','embed','param'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements=[]; self.root_depth=None; self.seen_scope=False; self.body=ReadableHTML()
        self.in_title=False; self.title_fragments=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs); classes=set(attributes.get('class','').split())
        if tag=='title': self.in_title=True
        in_primary=any('moment-detail__main-inner' in entry[1] for entry in self.elements)
        if self.root_depth is None and not self.seen_scope and in_primary and 'tap-rich-content__wrapper' in classes and 'moment-detail__module-space' in classes:
            self.root_depth=len(self.elements); self.seen_scope=True
        if self.root_depth is not None: self.body.handle_starttag(tag,attrs)
        if tag not in self.void_tags: self.elements.append((tag,classes))
    def handle_endtag(self,tag):
        if tag=='title': self.in_title=False
        if self.root_depth is not None: self.body.handle_endtag(tag)
        matching=next((i for i in range(len(self.elements)-1,-1,-1) if self.elements[i][0]==tag),None)
        if matching is not None:
            if self.root_depth is not None and matching<=self.root_depth: self.root_depth=None
            del self.elements[matching:]
    def handle_data(self,data):
        if self.in_title: self.title_fragments.append(data)
        elif self.root_depth is not None: self.body.handle_data(data)
    def readable(self):
        if not self.seen_scope: raise GuideImportError('未识别当前文章正文，已拒绝导入旁栏或推荐内容；动态/验证页面请手动教学')
        _,text=self.body.readable()
        if len(text)<80: raise GuideImportError('当前文章可读正文不足（可能主要是图片）；本版不做图片知识解析，请手动教学')
        title=re.sub(r'\s+',' ',''.join(self.title_fragments)).strip()[:240]
        return title,text

def readable_document(decoded,url):
    host=(urlsplit(url).hostname or '').lower()
    if host=='taptap.cn' or host.endswith('.taptap.cn'):
        parser=TapTapCurrentPost()
    else: parser=ReadableHTML()
    parser.feed(decoded)
    return parser.readable()

def fetch_guide(url):
    deadline=time.monotonic()+TOTAL_TIMEOUT
    current=url
    try:
        for redirect in range(MAX_REDIRECTS+1):
            if time.monotonic()>=deadline: raise GuideImportError('抓取超时')
            target=public_target(current)
            timeout=max(.1,deadline-time.monotonic())
            connection=PinnedConnection(target,timeout)
            try:
                connection.request('GET',target.path,headers={'Host':('['+target.host+']' if ':' in target.host else target.host),'User-Agent':'GameCompanion/0.3 explicit-guide-import','Accept':'text/html,text/plain','Accept-Encoding':'identity','Connection':'close'})
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    if redirect==MAX_REDIRECTS: raise GuideImportError('攻略网址跳转次数过多')
                    location=response.getheader('Location')
                    if not location: raise GuideImportError('攻略跳转无目标网址')
                    current=urljoin(target.url,location)
                    continue
                if response.status!=200: raise GuideImportError('攻略网站未返回可读正文（HTTP '+str(response.status)+'）')
                content_type=response.getheader('Content-Type','').lower()
                mime=content_type.split(';',1)[0].strip()
                if mime not in ('text/html','text/plain','application/xhtml+xml'): raise GuideImportError('仅支持公开 HTML 或纯文本攻略；PDF/图片/登录页面需手动教学')
                if response.getheader('Content-Encoding','identity').lower() not in ('identity',''): raise GuideImportError('攻略网站返回压缩内容，当前安全导入不支持')
                length=response.getheader('Content-Length')
                if length and (not length.isdigit() or int(length)>MAX_BYTES): raise GuideImportError('攻略正文超出2MB大小限制')
                body=bytearray()
                while True:
                    remaining=deadline-time.monotonic()
                    if remaining<=0: raise GuideImportError('抓取超时')
                    if connection.sock: connection.sock.settimeout(remaining)
                    chunk=response.read(min(65536,MAX_BYTES-len(body)+1))
                    if not chunk: break
                    body.extend(chunk)
                    if len(body)>MAX_BYTES: raise GuideImportError('攻略正文超出2MB大小限制')
                match=re.search(r'charset\s*=\s*[\"\']?([a-zA-Z0-9_\-]+)',content_type)
                encoding=match.group(1) if match else 'utf-8'
                try: decoded=bytes(body).decode(encoding,errors='replace')
                except LookupError: raise GuideImportError('攻略正文字符编码不支持') from None
                if mime=='text/plain': title=target.host; readable=decoded.strip()
                else:
                    title,readable=readable_document(decoded,target.url)
                    title=title or target.host
                if len(readable)<80: raise GuideImportError('未找到足够可读正文；动态/登录/付费页面请手动教学')
                truncated=len(readable)>MAX_TEXT
                readable=readable[:MAX_TEXT]
                return GuideDocument(target.url,title,readable,time.time(),hashlib.sha256(bytes(body)).hexdigest(),truncated)
            finally: connection.close()
    except GuideImportError: raise
    except (OSError,http.client.HTTPException,ValueError): raise GuideImportError('公开攻略抓取失败或超时，未导入') from None
    raise GuideImportError('攻略抓取未完成')

def guide_chunks(document,limit=24):
    """Deterministic text leaves; never claim source correctness or inferred version."""
    chunks=[]; current=[]; size=0
    for paragraph in re.split(r'\n\s*\n',document.text):
        paragraph=paragraph.strip()
        if not paragraph: continue
        # Preserve paragraph order while bounding each leaf; whole text retained in source record.
        for offset in range(0,len(paragraph),1100):
            part=paragraph[offset:offset+1100]
            if current and size+len(part)>1100:
                chunks.append('\n\n'.join(current)); current=[]; size=0
            current.append(part); size+=len(part)
            if len(chunks)>=limit: return chunks[:limit]
    if current: chunks.append('\n\n'.join(current))
    return chunks[:limit]

def relevance(query,item):
    haystack=' '.join(str(item.get(k,'')) for k in ('problem','steps','advice','source_title')).casefold()
    tokens=set(re.findall(r'[a-z0-9_]{3,}|[\u4e00-\u9fff]{2,}',query.casefold()))
    expanded=set()
    for token in tokens:
        if re.fullmatch(r'[\u4e00-\u9fff]+',token): expanded.update(token[i:i+2] for i in range(len(token)-1))
        else: expanded.add(token)
    return sum(1 for token in expanded if token in haystack)
