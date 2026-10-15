"""Behavioral checks for v3 provider boundaries and migration; no real screenshot/model calls."""
import base64, json, threading, time
from pathlib import Path
import httpx
import pytest
from app import webserver as web
from app.providers import DemoProvider, ProviderRequest, CodexCLIProvider, ProviderError

@pytest.fixture
def live(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'ROOT',tmp_path); web.initialize()
    server=web.Server(('127.0.0.1',0),web.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    client=httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',trust_env=False,timeout=3)
    client.headers['X-Companion-Token']=client.get('/api/state').json()['token']
    yield client
    client.close(); server.shutdown(); server.server_close()

def test_office_game_and_task_memory_isolation(live):
    office={'scope':'office','application':'excel.exe','task':'budget','game':'','version':'','scene':'sheet','problem':'total'}
    assert live.post('/api/memory',json={**office,'advice':'SUM all rows'}).status_code==200
    sid=live.post('/api/session',json={}).json()['session']
    def ask(ctx):
        response=live.post('/api/chat',json={'session':sid,'text':'hello','mode':'demo',**ctx})
        assert response.status_code==200
        blocks=response.text.split('\n\n')
        return [json.loads(x.split('data: ',1)[1]) for x in blocks if x.startswith('event: evidence')][0]
    assert len(ask(office))==1
    assert ask({**office,'application':'word.exe'})==[]
    assert ask({**office,'task':'other'})==[]
    assert ask({**office,'scope':'game','game':'test','version':'v1','application':''})==[]

def test_remote_image_requires_explicit_authorization(live):
    sid=live.post('/api/session',json={}).json()['session']
    image='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8test').decode()
    response=live.post('/api/chat',json={'session':sid,'text':'hello','mode':'codex','image':image})
    assert response.status_code==400
    assert '授权' in response.json()['error']

def test_demo_does_not_claim_image_inference():
    request=ProviderRequest([],image='arbitrary image',text='what?')
    assert '不理解画面' in ''.join(DemoProvider().stream(request,threading.Event()))
    assert ''.join(DemoProvider().stream(ProviderRequest([],automatic=True),threading.Event()))=='[[SILENT]]'

def test_cli_transient_files_and_read_only_arguments(tmp_path,monkeypatch):
    from app import providers
    monkeypatch.setattr(providers,'__file__',str(tmp_path/'app'/'providers'/'__init__.py'))
    executable=tmp_path/'codex.exe'; executable.write_bytes(b'fake executable')
    seen={}
    class Process:
        returncode=0
        def __init__(self,args,**kwargs):
            seen.update(args=args,kwargs=kwargs)
            seen['folder']=Path(kwargs['cwd']); seen['image']=Path(args[args.index('--image')+1])
            assert seen['image'].read_bytes()==b'\xff\xd8sample'
            Path(args[args.index('-o')+1]).write_text('模型测试答复',encoding='utf-8')
        def poll(self): return 0
    monkeypatch.setattr(providers.subprocess,'Popen',Process)
    request=ProviderRequest([],image='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8sample').decode())
    result=''.join(CodexCLIProvider({'executable':str(executable)}).stream(request,threading.Event()))
    assert result=='模型测试答复'
    assert seen['kwargs']['shell'] is False
    assert '--ephemeral' in seen['args'] and 'read-only' in seen['args'] and 'features.shell_tool=false' in seen['args']
    assert not seen['image'].exists() and not seen['folder'].exists()

def test_cli_cancel_before_spawn(tmp_path,monkeypatch):
    from app import providers
    monkeypatch.setattr(providers.subprocess,'Popen',lambda *a,**k:pytest.fail('canceled request spawned process'))
    stop=threading.Event(); stop.set()
    assert list(CodexCLIProvider({}).stream(ProviderRequest([]),stop))==[]

@pytest.mark.parametrize('changed_application',['changed-editor.exe','pid:12345'])
def test_region_rejects_process_image_identity_change(changed_application):
    from app.window_source import WindowsSource
    from app.observation import WindowInfo
    source=WindowsSource.__new__(WindowsSource)
    old=WindowInfo(10,'Document A',12345,'editor.exe')
    source.foreground=lambda: (WindowInfo(10,'Document A',12345,changed_application),'')
    region,reason=source.region(old)
    assert region is None and '应用身份已变化' in reason


def test_cli_permission_error_is_distinguished_without_disclosing_raw_diagnostics(tmp_path):
    from app.providers import cli_failure_message
    path=tmp_path/'errors.tmp'
    path.write_text('Error: failed to initialize in-process app-server client: 拒绝访问。 (os error 5)\nPRIVATE_DIAGNOSTIC_SENTINEL',encoding='utf-8')
    message=cli_failure_message(path)
    assert '初始化权限不足' in message and '模型请求前失败' in message
    assert 'PRIVATE_DIAGNOSTIC_SENTINEL' not in message and '(os error 5)' not in message

def test_product_url_import_is_explicit_classified_and_refreshes_same_source(live,monkeypatch):
    from app.guide_import import GuideDocument
    ctx={'scope':'game','game':'Test Game','version':'historical-v1','task':'boss route','scene':'guide poster','problem':'boss timing','application':''}
    document=GuideDocument('https://example.com/guide','Public Boss Guide',('At stage three use a shield before the boss attack. '*20),time.time(),'a'*64,False)
    calls=[]
    def fetch(url): calls.append(url); return document
    monkeypatch.setattr(web,'fetch_guide',fetch)
    response=live.post('/api/memory/import',json={'url':document.url,**ctx})
    assert response.status_code==400 and not calls
    response=live.post('/api/memory/import',json={'url':document.url,'explicit_teaching':True,**ctx})
    assert response.status_code==200
    result=response.json(); assert result['classification']==ctx and result['leaves']>=1
    state=live.get('/api/state').json(); count=len(state['memories'])
    assert state['memories'][0]['source_url']==document.url and state['memories'][0]['fetched_at']==document.fetched_at
    assert 'shield' in state['memories'][0]['steps']
    refreshed=live.post('/api/memory/import',json={'url':document.url,'explicit_teaching':True,**ctx})
    assert refreshed.status_code==200 and len(live.get('/api/state').json()['memories'])==count
    changed={**ctx,'version':'historical-v2'}
    assert live.post('/api/memory/import',json={'url':document.url,'explicit_teaching':True,**changed}).status_code==200
    assert len(live.get('/api/state').json()['memories'])==2*count

def test_imported_leaf_retrieval_uses_current_question_and_keeps_version_boundary(live,monkeypatch):
    from app.guide_import import GuideDocument
    ctx={'scope':'game','game':'Test Game','version':'v1','task':'route','scene':'boss','problem':'mechanics','application':''}
    text=('Opening economy '+'x'*1000)+'\n\n'+('Boss shield timing '+'y'*1000)+'\n\n'+('Final rewards '+'z'*1000)
    monkeypatch.setattr(web,'fetch_guide',lambda url:GuideDocument(url,'Guide',text,time.time(),'b'*64,False))
    assert live.post('/api/memory/import',json={'url':'https://example.com/guide','explicit_teaching':True,**ctx}).status_code==200
    session=live.post('/api/session',json={}).json()['session']
    response=live.post('/api/chat',json={'session':session,'text':'What is boss shield timing?','mode':'demo',**ctx})
    evidence=[json.loads(x.split('data: ',1)[1]) for x in response.text.split('\n\n') if x.startswith('event: evidence')][0]
    assert evidence and 'Boss shield timing' in evidence[0]['steps']
    assert sum(len(json.dumps(x,ensure_ascii=False)) for x in evidence)<=2400
    response=live.post('/api/chat',json={'session':session,'text':'boss shield timing','mode':'demo',**{**ctx,'version':'v2'}})
    evidence=[json.loads(x.split('data: ',1)[1]) for x in response.text.split('\n\n') if x.startswith('event: evidence')][0]
    assert evidence==[]

@pytest.mark.parametrize('edit',[
    {'steps':'User independently corrected tactic','version':'S19','source':'用户独立纠正'},
    {'problem':'Renamed question'},
    {'scene':'Different scene','task':'Different task'},
])
def test_manual_edit_of_imported_leaf_detaches_web_provenance_only_for_that_leaf(live,monkeypatch,edit):
    from app.guide_import import GuideDocument
    ctx={'scope':'game','game':'Test Game','version':'S18','task':'Original task','scene':'Original scene','problem':'Original question','application':''}
    url='https://guide.example/article'
    text=('Original first source tactic '+'a'*1000)+'\n\n'+('Original second source tactic '+'b'*1000)
    monkeypatch.setattr(web,'fetch_guide',lambda requested:GuideDocument(url,'Original source title',text,time.time(),'c'*64,False))
    imported=live.post('/api/memory/import',json={'url':url,'explicit_teaching':True,**ctx})
    assert imported.status_code==200
    originals=live.get('/api/state').json()['memories']; assert len(originals)==2
    original=originals[0]; sibling=originals[1]
    source_id=original['source_id']
    with web.connect() as database:
        original_source=dict(database.execute('SELECT * FROM guide_sources WHERE id=?',(source_id,)).fetchone())
    edited={**original,**edit}
    assert live.post('/api/memory',json=edited).status_code==200
    rows={x['id']:x for x in live.get('/api/state').json()['memories']}
    result=rows[original['id']]
    assert result['source_id'] is None and result['source_url']=='' and result['source_title']=='' and result['fetched_at']==0
    assert result['source_kind']=='user-edit' and '用户编辑教学' in result['source'] and url not in result['source']
    assert rows[sibling['id']]==sibling
    with web.connect() as database:
        preserved=dict(database.execute('SELECT * FROM guide_sources WHERE id=?',(source_id,)).fetchone())
    assert preserved==original_source
    if 'version' in edit or 'scene' in edit or 'task' in edit:
        session=live.post('/api/session',json={}).json()['session']
        response=live.post('/api/chat',json={'session':session,'text':'What is the independently corrected tactic?','mode':'demo',**{key:result[key] for key in ctx}})
        evidence=[json.loads(x.split('data: ',1)[1]) for x in response.text.split('\n\n') if x.startswith('event: evidence')][0]
        assert len(evidence)==1 and evidence[0]['source_kind']=='user-edit'
        assert evidence[0]['source_url']=='' and url not in evidence[0]['source']
