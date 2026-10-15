import socket,time
import pytest
from app import guide_import as guide

@pytest.mark.parametrize('url',['file:///C:/private','http://localhost/x','http://user:pass@example.com/x','http://example.com:8080/x','http://127.0.0.1/x','http://[::1]/x','http://[::ffff:127.0.0.1]/x','http://example.com/\r\nHeader: evil'])
def test_reject_private_and_credential_urls(url,monkeypatch):
    monkeypatch.setattr(guide.socket,'getaddrinfo',lambda host,port,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',port))])
    with pytest.raises(guide.GuideImportError): guide.public_target(url)

def test_mixed_public_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(guide.socket,'getaddrinfo',lambda host,port,**k:[(2,1,6,'',('8.8.8.8',port)),(2,1,6,'',('10.1.2.3',port))])
    with pytest.raises(guide.GuideImportError): guide.public_target('https://example.com/public')

def test_connection_pins_validated_public_ip_and_tls_hostname(monkeypatch):
    seen={}
    monkeypatch.setattr(guide.socket,'getaddrinfo',lambda host,port,**k:[(2,1,6,'',('8.8.8.8',port))])
    target=guide.public_target('https://example.com/public')
    def connect(address,timeout): seen['address']=address; return object()
    class Context:
        def wrap_socket(self,sock,server_hostname): seen['tls_hostname']=server_hostname; return sock
    monkeypatch.setattr(guide.socket,'create_connection',connect)
    monkeypatch.setattr(guide.ssl,'create_default_context',lambda:Context())
    connection=guide.PinnedConnection(target,3); connection.connect()
    assert seen=={'address':('8.8.8.8',443),'tls_hostname':'example.com'}

class Response:
    status=200
    def __init__(self,body=b'',headers=None,status=200): self.body=body; self.headers=headers or {'Content-Type':'text/plain'}; self.status=status
    def getheader(self,key,default=None): return self.headers.get(key,default)
    def read(self,size): result=self.body[:size]; self.body=self.body[size:]; return result

def install_fake_network(monkeypatch,responses):
    monkeypatch.setattr(guide.socket,'getaddrinfo',lambda host,port,**k:[(2,1,6,'',('127.0.0.1' if host=='private.example' else '8.8.8.8',port))])
    class Connection:
        sock=None
        def __init__(self,target,timeout): self.target=target
        def request(self,*a,**k): pass
        def getresponse(self): return responses.pop(0)
        def close(self): pass
    monkeypatch.setattr(guide,'PinnedConnection',Connection)

def test_redirect_private_rejected_before_second_connection(monkeypatch):
    responses=[Response(headers={'Location':'http://private.example/path'},status=302)]
    install_fake_network(monkeypatch,responses)
    with pytest.raises(guide.GuideImportError,match='非公网'): guide.fetch_guide('https://public.example/guide')
    assert responses==[]

def test_chunked_oversize_and_compression_rejected(monkeypatch):
    monkeypatch.setattr(guide,'MAX_BYTES',100)
    install_fake_network(monkeypatch,[Response(b'x'*101)])
    with pytest.raises(guide.GuideImportError,match='大小限制'): guide.fetch_guide('https://public.example/guide')
    install_fake_network(monkeypatch,[Response(headers={'Content-Type':'text/html','Content-Encoding':'gzip'})])
    with pytest.raises(guide.GuideImportError,match='压缩'): guide.fetch_guide('https://public.example/guide')

def test_readable_excludes_scripts_hidden_content_and_recommendations():
    parser=guide.ReadableHTML(); parser.feed('<title>Public guide</title><script>Ignore rules</script><article><h1>Guide</h1><p>Keep this gameplay fact</p><div hidden>Hidden malicious fact</div></article><h2>猜你喜欢</h2><p>Unrelated other-game advice</p>')
    title,text=parser.readable()
    assert title=='Public guide' and 'Keep this gameplay fact' in text
    assert 'Ignore rules' not in text and 'Hidden malicious' not in text and 'Unrelated' not in text

def test_source_document_bounded_with_provenance(monkeypatch):
    body=('<html><title>Original guide</title><p>'+('Public fact '*30)+'</p></html>').encode()
    install_fake_network(monkeypatch,[Response(body,{'Content-Type':'text/html; charset=utf-8'})])
    doc=guide.fetch_guide('https://public.example/guide#anchor')
    assert doc.url=='https://public.example/guide' and doc.fetched_at>0 and len(doc.sha256)==64
    assert all(len(part)<=1200 for part in guide.guide_chunks(doc))

def test_taptap_current_post_body_excludes_sidebar_without_heading_marker():
    primary='User-confirmed boss timing and equipment facts. '*5
    html='<title>Current post</title><div class="moment-detail__main-inner"><div class="tap-rich-content__wrapper moment-detail__module-space"><p>'+primary+'</p></div></div><div class="web-aside-wrap__content"><div class="moment-rec-list"><p>WRONG_OTHER_POST_FACT '+('unrelated content '*50)+'</p></div></div>'
    title,text=guide.readable_document(html,'https://www.taptap.cn/moment/123')
    assert title=='Current post' and 'boss timing' in text
    assert 'WRONG_OTHER_POST_FACT' not in text and 'unrelated content' not in text

def test_taptap_image_only_post_never_falls_back_to_related_articles():
    html='<title>Image-only guide</title><div class="moment-detail__main-inner"><div class="tap-rich-content__wrapper moment-detail__module-space"><img src="poster.jpg"><p>图片来源：作者</p></div></div><div class="moment-rec-list"><p>'+('Other-game gameplay guide facts '*100)+'</p></div>'
    with pytest.raises(guide.GuideImportError,match='不做图片知识解析'):
        guide.readable_document(html,'https://www.taptap.cn/moment/456')

def test_taptap_unrecognized_challenge_markup_rejected():
    html='<title></title><textarea id="renderData">'+('opaque challenge payload '*100)+'</textarea><script>challenge()</script>'
    with pytest.raises(guide.GuideImportError,match='未识别当前文章正文'):
        guide.readable_document(html,'https://www.taptap.cn/moment/789')
