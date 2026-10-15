"""Native foreground spectator. Images are in memory except transient CLI image files."""
import argparse
from pathlib import Path
from .window_source import DEFAULT_EXCLUSIONS
import base64
import json
import math
import threading
import time
import httpx
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QByteArray, QBuffer, QIODevice, QPoint, QRect, QLocale
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QGuiApplication, QTextCursor, QFontDatabase
from PySide6.QtWidgets import (QApplication,QWidget,QMainWindow,QVBoxLayout,QHBoxLayout,QFormLayout,
    QLabel,QPushButton,QLineEdit,QComboBox,QCheckBox,QTextEdit,QTabWidget,QTreeWidget,QTreeWidgetItem,QMenu,QScrollArea)
from PySide6.QtTextToSpeech import QTextToSpeech
from .observation import ObservationPolicy
from .window_source import WindowsSource
from .pet3d.config import PetConfig
from .pet3d.window import PetWindow

_UI_INITIALIZED=False

def initialize_ui(app):
    global _UI_INITIALIZED
    if _UI_INITIALIZED: return
    _UI_INITIALIZED=True
    from pathlib import Path
    font_path=Path('C:/Windows/Fonts/msyh.ttc')
    if font_path.exists(): QFontDatabase.addApplicationFont(str(font_path))
    app.setFont(QFont('Microsoft YaHei',10))
    app.setStyleSheet('''
        QWidget { background:#142439; color:#dfedf5; font-family:"Microsoft YaHei"; font-size:13px; }
        QMainWindow { background:#142439; }
        QLineEdit, QTextEdit, QTreeWidget, QComboBox { background:#0d1b2e; color:#e4f3fa; border:1px solid #365570; border-radius:6px; padding:6px; selection-background-color:#287f96; }
        QPushButton { background:#25465e; color:#dff8fb; border:1px solid #467a93; border-radius:6px; padding:7px; }
        QPushButton:hover { background:#306179; }
        QPushButton:pressed { background:#183447; }
        QTabWidget::pane { border:1px solid #365570; border-radius:7px; }
        QTabBar::tab { background:#1b344b; color:#b8d2e1; padding:9px 18px; }
        QTabBar::tab:selected { background:#2a5369; color:#cafff6; }
        QHeaderView::section { background:#25465e; color:#dff8fb; border:0; padding:7px; }
        QComboBox QAbstractItemView { background:#142439; color:#e4f3fa; selection-background-color:#306179; }
        QCheckBox { spacing:6px; }
        QMenu { background:#142439; border:1px solid #467a93; }
        QMenu::item:selected { background:#306179; }
        QScrollBar:vertical { background:#102036; width:11px; }
        QScrollBar::handle:vertical { background:#365570; min-height:20px; }
    ''')

class ApiWorker(QObject):
    event = Signal(int,str,object)
    finished = Signal(int)
    def __init__(self, url, token, payload, generation):
        super().__init__()
        self.url,self.token,self.payload,self.generation=url,token,payload,generation
        self.stop=threading.Event()
        self.client=None
        self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
    def cancel(self):
        self.stop.set()
        def close():
            try:
                if self.client: self.client.close()
                with httpx.Client(timeout=2,trust_env=False) as client:
                    client.post(self.url+'/api/cancel',headers={'X-Companion-Token':self.token},json={
                        'session':self.payload['session'],'turn':self.payload['turn']})
            except Exception: pass
        threading.Thread(target=close,daemon=True).start()
    def run(self):
        try:
            with httpx.Client(timeout=httpx.Timeout(190,connect=3),trust_env=False) as client:
                self.client=client
                if self.stop.is_set(): return
                with client.stream('POST',self.url+'/api/chat',headers={'X-Companion-Token':self.token},json=self.payload) as response:
                    if response.status_code != 200:
                        response.read()
                        raise RuntimeError(response.json().get('error','本地 API 请求失败'))
                    kind='message'; data=[]; terminal=False
                    for line in response.iter_lines():
                        if self.stop.is_set(): break
                        if line.startswith('event:'): kind=line[6:].strip()
                        elif line.startswith('data:'): data.append(line[5:].strip())
                        elif not line and data:
                            self.event.emit(self.generation,kind,json.loads('\n'.join(data)))
                            if kind in ('done','error'): terminal=True
                            data=[]
                    if not terminal and not self.stop.is_set():
                        self.event.emit(self.generation,'error',{'error':'回答中断，未收到完成事件'})
        except Exception as exc:
            if not self.stop.is_set(): self.event.emit(self.generation,'error',{'error':str(exc)[:220]})
        finally:
            self.client=None
            self.payload['image']=''
            self.finished.emit(self.generation)

class GuideImportWorker(QObject):
    result=Signal(int,bool,object)
    def __init__(self,url,token,payload,generation):
        super().__init__(); self.url=url; self.token=token; self.payload=payload; self.generation=generation; self.thread=None
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True); self.thread.start()
    def run(self):
        try:
            with httpx.Client(timeout=httpx.Timeout(25,connect=3),trust_env=False) as client:
                response=client.post(self.url+'/api/memory/import',headers={'X-Companion-Token':self.token},json=self.payload)
                data=response.json()
                if response.status_code!=200: raise RuntimeError(data.get('error','攻略导入失败'))
                self.result.emit(self.generation,True,data)
        except Exception as exc:
            message=str(exc)[:200] if isinstance(exc,RuntimeError) else '攻略导入连接失败或超时，未确认导入结果'
            self.result.emit(self.generation,False,{'error':message})

class Speech(QObject):
    changed=Signal(bool)
    status=Signal(str)
    def __init__(self):
        super().__init__(); self.engine=None; self.muted=False
        for name in ('sapi','winrt'):
            if name not in QTextToSpeech.availableEngines(): continue
            candidate=QTextToSpeech(name,self)
            # Qt's availableVoices() is scoped to the selected locale. SAPI may
            # report no voices until setLocale() explicitly initializes that list.
            chinese=[]
            locales=[locale for locale in candidate.availableLocales()
                     if locale.name().startswith('zh')]
            locales.sort(key=lambda locale: locale.name() != 'zh_CN')
            for locale in locales:
                candidate.setLocale(locale)
                chinese=[voice for voice in candidate.availableVoices()
                         if voice.locale().name().startswith('zh')]
                if chinese: break
            if chinese and candidate.state()!=QTextToSpeech.State.Error:
                self.engine=candidate; candidate.setVoice(chinese[0]); candidate.setRate(.05)
                candidate.stateChanged.connect(self.on_state)
                candidate.errorOccurred.connect(self.on_error)
                break
            candidate.deleteLater()
        self.description=('本地中文语音：'+self.engine.engine() if self.engine else
            '字幕回退：SAPI / WinRT 未找到可用中文语音。正常 Windows 账号启动后可重新检测；可安装系统中文语音。')
    def on_state(self,state): self.changed.emit(state==QTextToSpeech.State.Speaking)
    def stop(self):
        if self.engine: self.engine.stop()
        self.changed.emit(False)
    def on_error(self,*_):
        engine=self.engine
        self.engine=None
        if engine: engine.stop()
        self.changed.emit(False)
        self.description='字幕回退：检测到中文声线，但本地语音输出失败，请检查 Windows 音频设备 / 权限。'
        self.status.emit(self.description)
    def say(self,text):
        if self.engine and not self.muted:
            self.engine.say(text)
            # Older Qt SAPI can fail synchronously without errorOccurred or an Error state.
            if self.engine and self.engine.errorString(): self.on_error()

class Pet(QWidget):
    """Pre-C03 2D pet, kept for reference and for anything that imports it directly.

    The live pet is `pet3d.window.PetWindow` (frameless, topmost, no taskbar, VRM via
    three.js, per-pixel click-through). Its 2D fallback is `pet3d.fallback2d.Pet2DFallback`,
    which draws the same character. This class is retained because docs/03 card 1 forbids
    deleting the 2D code path.
    """
    settings_requested=Signal()
    pause_requested=Signal()
    interrupt_requested=Signal()
    mute_requested=Signal()
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280,310); self.phase=0; self.state='idle'; self.caption='双击打开设置 · 右键控制'; self.drag=None
        self.timer=QTimer(self); self.timer.timeout.connect(self.animate); self.timer.start(70)
        screen=QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right()-310,screen.bottom()-340)
    def animate(self): self.phase+=.17; self.update()
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(13,24,42,225)); p.drawRoundedRect(10,10,260,90,18,18)
        p.setPen(QColor('#d8edf6')); p.setFont(QFont('Microsoft YaHei',10))
        p.drawText(QRect(22,18,235,75),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.caption[:100])
        y=145+int(5*math.sin(self.phase)); p.setPen(QPen(QColor('#52dbdc'),3))
        p.setBrush(QColor('#152d46')); p.drawRoundedRect(60,y,160,96,30,30)
        p.setBrush(QColor('#284d69')); p.drawRoundedRect(84,y+95,112,42,18,18)
        p.drawLine(140,y,140,y-22); p.setBrush(QColor('#ffe09a')); p.drawEllipse(QPoint(140,y-26),7,7)
        p.setPen(QPen(QColor('#97ffec'),5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        blink=math.sin(self.phase*.21)>.985
        if self.state=='thinking':
            p.drawEllipse(QPoint(108,y+40),9,9); p.drawEllipse(QPoint(174,y+40),9,9)
            p.setPen(QColor('#ffe09a')); p.drawText(235,y+5,'···')
        elif blink:
            p.drawLine(99,y+40,117,y+40); p.drawLine(165,y+40,183,y+40)
        else:
            p.drawLine(108,y+33,108,y+47); p.drawLine(174,y+33,174,y+47)
        p.setBrush(QColor('#97ffec')); p.setPen(Qt.PenStyle.NoPen)
        if self.state=='speaking': p.drawEllipse(QRect(128,y+62,26,5+int(13*abs(math.sin(self.phase*2)))))
        else: p.drawRoundedRect(126,y+66,29,5,2,2)
        p.setPen(QPen(QColor('#52dbdc'),8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        p.drawLine(83,y+110,57,y+118)
        p.drawLine(196,y+109,226,y+int(110+12*math.sin(self.phase)))
        p.setPen(QColor('#9ab5c7')); p.setFont(QFont('Microsoft YaHei',9)); p.drawText(QRect(20,282,240,24),Qt.AlignmentFlag.AlignCenter,'星澜  ·  只旁观，操作接口禁用')
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton: self.drag=event.globalPosition().toPoint()-self.pos()
    def mouseMoveEvent(self,event):
        if self.drag is not None: self.move(event.globalPosition().toPoint()-self.drag)
    def mouseReleaseEvent(self,event): self.drag=None
    def mouseDoubleClickEvent(self,event): self.settings_requested.emit()
    def contextMenuEvent(self,event):
        menu=QMenu(self)
        for label,signal in [('设置 / 聊天',self.settings_requested),('暂停观察',self.pause_requested),('打断回答',self.interrupt_requested),('静音 / 恢复语音',self.mute_requested)]:
            menu.addAction(label,signal.emit)
        menu.addSeparator(); menu.addAction('退出桌宠',QApplication.instance().quit); menu.exec(event.globalPos())

class Companion(QMainWindow):
    def __init__(self,port):
        super().__init__(); initialize_ui(QApplication.instance()); self.url=f'http://127.0.0.1:{port}'
        self.policy=ObservationPolicy(); self.source=WindowsSource(); self.selected=None
        self.target_valid=False; self.target_key=None; self.contexts={}; self.active_app=""; self.updating=False; self.pause_reason=""; self.import_worker=None
        self.worker=None; self.pending_manual=None; self.token=''; self.session=''; self.turn=0; self.answer=''; self.auto=False
        self.speech=Speech(); self.pet=PetWindow(PetConfig.load()); self.pet.show()
        self.speech.changed.connect(self.speaking); self.speech.status.connect(self.status)
        self.pet.settings_requested.connect(self.show_settings); self.pet.pause_requested.connect(self.pause)
        self.pet.interrupt_requested.connect(self.interrupt); self.pet.mute_requested.connect(self.toggle_mute)
        self.pet.quit_requested.connect(QApplication.instance().quit)
        self.pet.status_changed.connect(self.status)
        self.setWindowTitle('星澜 · 桌宠设置与聊天')
        available=QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(710,max(360,available.width()-40)),min(720,max(480,available.height()-60)))
        tabs=QTabWidget(); self.setCentralWidget(tabs)
        panel=QWidget(); layout=QVBoxLayout(panel); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(panel); tabs.addTab(scroll,'旁观与聊天')
        title=QLabel('默认跟随前台办公 / 游戏窗口；明确开启后每5秒仅采集该窗口。默认本地测试不向远程发送画面。敏感标题排除不保证识别全部隐私。'); title.setWordWrap(True); layout.addWidget(title)
        row=QHBoxLayout(); self.follow=QComboBox(); self.follow.addItem('自动跟随前台窗口','foreground'); self.follow.addItem('锁定游戏窗口','locked'); row.addWidget(self.follow)
        self.windows=QComboBox(); row.addWidget(self.windows,1)
        refresh=QPushButton('刷新窗口'); refresh.clicked.connect(self.refresh_windows); row.addWidget(refresh); layout.addLayout(row)
        self.windows.currentIndexChanged.connect(self.select_window)
        self.follow.currentIndexChanged.connect(self.tracking_changed)
        self.target_label=QLabel('前台目标：尚未开启观察'); self.target_label.setTextFormat(Qt.TextFormat.PlainText); self.target_label.setWordWrap(True); layout.addWidget(self.target_label)
        self.scope=QComboBox(); self.scope.addItem('办公陪伴','office'); self.scope.addItem('游戏旁观','game'); self.scope.currentIndexChanged.connect(self.context_changed)
        layout.addWidget(self.scope)
        self.fields={}; form=QFormLayout()
        for key,label in [('task','任务'),('game','游戏（办公可留空）'),('version','赛季 / 版本'),('scene','场景'),('problem','关注问题')]:
            field=QLineEdit(); self.fields[key]=field; field.textChanged.connect(self.context_changed); form.addRow(label,field)
        layout.addLayout(form)
        options=QHBoxLayout(); self.mode=QComboBox(); self.mode.addItem('本地测试（非真正 AI）','demo'); self.mode.addItem('Codex CLI（需正常账号登录）','codex'); self.mode.addItem('OpenAI 兼容 / Qwen（需配置）','openai'); self.mode.addItem('手工回放（非后台 AI）','replay'); options.addWidget(self.mode)
        self.style=QComboBox()
        for name,key in [('安静 · 45秒','quiet'),('平衡 · 20秒','balanced'),('活跃 · 8秒','active')]: self.style.addItem(name,key)
        self.style.setCurrentIndex(1); options.addWidget(self.style); layout.addLayout(options)
        self.allow_images=QCheckBox('允许当前真实 provider 发送目标窗口截图（Codex可能为云端）'); self.allow_images.setChecked(False); self.allow_images.toggled.connect(self.context_changed); layout.addWidget(self.allow_images)
        self.provider_label=QLabel(); self.provider_label.setTextFormat(Qt.TextFormat.PlainText); self.provider_label.setWordWrap(True); layout.addWidget(self.provider_label)
        self.exclusions=QTextEdit(); self.exclusions.setMaximumHeight(65); self.exclusions.setPlainText('\n'.join(DEFAULT_EXCLUSIONS)); layout.addWidget(QLabel('排除标题 / 应用关键词（每行一条，可编辑；无法完整识别隐私）')); layout.addWidget(self.exclusions)
        exclusion_path=Path(__file__).resolve().parent.parent/'runtime'/'exclusions.json'
        self.exclusion_path=exclusion_path
        if exclusion_path.exists():
            try:
                items=json.loads(exclusion_path.read_text(encoding='utf-8')); self.exclusions.setPlainText('\n'.join(items))
            except Exception: pass
        self.source.exclusions=self.exclusions.toPlainText().splitlines(); self.exclusions.textChanged.connect(self.exclusions_changed)
        self.mode.currentIndexChanged.connect(self.context_changed); self.style.currentIndexChanged.connect(self.style_changed)
        row=QHBoxLayout(); self.watch=QPushButton('开始观察'); self.watch.clicked.connect(self.toggle_watch); row.addWidget(self.watch)
        stop=QPushButton('打断'); stop.clicked.connect(self.interrupt); row.addWidget(stop)
        self.mute=QCheckBox('静音'); self.mute.toggled.connect(self.mute_changed); row.addWidget(self.mute)
        self.personality=QCheckBox('自然陪伴评论'); self.personality.setChecked(True); self.personality.toggled.connect(self.context_changed); row.addWidget(self.personality); layout.addLayout(row)
        speech_label=QLabel(self.speech.description); speech_label.setWordWrap(True); layout.addWidget(speech_label)
        from .pet3d import vrm_loader as _vrm_loader
        _model_entry=_vrm_loader.REGISTRY.get(self.pet.config.model or _vrm_loader.DEFAULT_MODEL)
        model_line=('3D 角色：'+_model_entry.title+' · '+_model_entry.author+' · '+_model_entry.license_name+'（'+_model_entry.license_url+'）') if _model_entry else ('3D 角色：用户导入模型 '+str(self.pet.config.model or '')+'（许可由导入者负责）')
        model_label=QLabel(model_line); model_label.setTextFormat(Qt.TextFormat.PlainText); model_label.setWordWrap(True); layout.addWidget(model_label)
        self.log=QTextEdit(); self.log.setReadOnly(True); layout.addWidget(self.log,1)
        self.prompt=QLineEdit(); self.prompt.setPlaceholderText('提问，或“记住：你的经验”'); self.prompt.returnPressed.connect(self.send_manual); layout.addWidget(self.prompt)
        row=QHBoxLayout(); send=QPushButton('发送（优先于自动观察）'); send.clicked.connect(self.send_manual); row.addWidget(send)
        reset=QPushButton('新会话'); reset.clicked.connect(self.reset_session); row.addWidget(reset); layout.addLayout(row)
        self.status_label=QLabel('连接本地服务…'); self.status_label.setTextFormat(Qt.TextFormat.PlainText); self.status_label.setWordWrap(True); layout.addWidget(self.status_label)
        self.build_memory(tabs)
        excluded=[self.source.exclude_from_capture(int(w.winId())) for w in (self.pet,self)]
        exclusion_label=QLabel('已请求系统排除桌宠 / 设置窗口截图' if all(excluded) else '当前系统无法排除桌宠叠层：请把桌宠放在游戏区域外，截图可能包含叠层')
        exclusion_label.setWordWrap(True); layout.addWidget(exclusion_label)
        self.refresh_windows(); self.update_provider_label()
        self.focus_timer=QTimer(self); self.focus_timer.timeout.connect(self.focus_guard); self.focus_timer.start(300)
        self.timer=QTimer(self); self.timer.timeout.connect(self.observe); self.timer.start(5000)
        QTimer.singleShot(0,self.connect_server)
    def append_log(self,text):
        cursor=self.log.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log.document().isEmpty(): cursor.insertText('\n')
        cursor.insertText(str(text)); self.log.setTextCursor(cursor); self.log.ensureCursorVisible()
    def update_provider_label(self):
        if not hasattr(self,'provider_label'): return
        name=self.mode.currentData(); allowed=self.allow_images.isChecked()
        self.provider_label.setText('Provider：'+self.mode.currentText()+' · '+('本地测试 / 回放，不向远程发图' if name in ('demo','replay') else ('截图可发送至该 provider' if allowed else '截图发送关闭；仅文字聊天')))
    def exclusions_changed(self):
        self.source.exclusions=[x.strip() for x in self.exclusions.toPlainText().splitlines() if x.strip()][:200]
        self.interrupt(); self.target_valid=False
        try:
            self.exclusion_path.parent.mkdir(exist_ok=True)
            self.exclusion_path.write_text(json.dumps(self.source.exclusions,ensure_ascii=False),encoding='utf-8')
        except OSError: self.status('排除规则已应用，本地保存失败')
    def tracking_changed(self):
        self.pause(); self.target_key=None; self.target_valid=False
        self.windows.setEnabled(self.follow.currentData()=='locked')
        self.selected=self.windows.currentData() if self.follow.currentData()=='locked' else None
        self.status('跟随模式已更新；再次开启观察后生效')
    def switch_context(self,window):
        application=window.application or ('pid:'+str(window.process_id))
        if self.active_app==application: return
        if self.active_app:
            self.contexts[self.active_app]=(self.scope.currentIndex(),{k:f.text() for k,f in self.fields.items()})
        self.active_app=application; self.updating=True
        index,values=self.contexts.get(application,(self.scope.currentIndex(),{k:f.text() for k,f in self.fields.items()}) if not self.contexts and len(self.active_app)>0 and self.target_key is None else (0,{}))
        self.scope.setCurrentIndex(index)
        for key,field in self.fields.items(): field.setText(values.get(key,''))
        self.updating=False
        self.log.clear(); self.memory_id=None
        try: self.session=self.api('/api/session',{})['session']
        except Exception: self.session=''; self.status('切应用后会话刷新失败，请重新连接')
    def refresh_target(self):
        if not self.policy.enabled: return False
        window,reason=self.source.foreground()
        if window and self.follow.currentData()=='locked':
            locked=self.windows.currentData()
            if not locked or (window.handle,window.process_id)!=(locked.handle,locked.process_id):
                window=None; reason='等待锁定游戏窗口回到前台'
        if not window:
            if self.target_valid or (self.worker and self.auto and not self.worker.stop.is_set()) or self.policy.latest is not None or (self.pet.state=='speaking' and self.auto): self.interrupt()
            self.target_valid=False
            self.target_label.setText('观察暂停：'+reason)
            if self.pause_reason!=reason: self.status('观察等待：'+reason); self.pause_reason=reason
            return False
        key=(window.handle,window.process_id,window.title,window.application)
        if key!=self.target_key:
            self.interrupt(); self.switch_context(window); self.target_key=key
        self.selected=window; self.target_valid=True; self.pause_reason=''
        self.target_label.setText('前台目标：'+window.application+' · '+window.title[:120])
        return True
    def focus_guard(self):
        if self.policy.enabled: self.refresh_target()
    def show_settings(self): self.show(); self.raise_(); self.activateWindow()
    def status(self,text): self.status_label.setText(text); self.pet.caption=text; self.pet.update()
    def api(self,path,payload=None):
        with httpx.Client(timeout=3,trust_env=False) as client:
            response=client.get(self.url+path) if payload is None else client.post(self.url+path,headers={'X-Companion-Token':self.token},json=payload)
            response.raise_for_status(); return response.json()
    def connect_server(self):
        try:
            state=self.api('/api/state'); self.token=state['token']; self.session=self.api('/api/session',{})['session']; self.render_memory(state['memories'])
            self.status('已连接 · 尚未采集画面 · 操作接口禁用')
        except Exception: self.status('本地服务未连接，请使用启动桌宠.ps1；可重新打开桌宠连接。')
    def refresh_windows(self):
        self.windows.blockSignals(True); self.windows.clear(); self.windows.addItem('可选：锁定一个游戏窗口',None)
        for item in self.source.windows(): self.windows.addItem(item.title,item)
        self.windows.blockSignals(False); self.select_window()
    def select_window(self,*_):
        if self.follow.currentData()!='locked':
            self.windows.setEnabled(False); return
        self.pause(); self.selected=self.windows.currentData(); self.target_key=None
        self.status('已选择锁定窗口，点击开始观察后才采集' if self.selected else '请选择一个锁定游戏窗口')
    def ctx(self):
        ctx={key:field.text().strip() for key,field in self.fields.items()}
        ctx['scope']=self.scope.currentData()
        ctx['application']=self.active_app if ctx['scope']=='office' else ''
        if ctx['scope']=='office': ctx['game']=''; ctx['version']=''
        return ctx
    def context_changed(self,*_):
        if self.updating: return
        self.interrupt(); self.update_provider_label(); self.status('上下文 / provider 已更新，旧结果已清除')
    def style_changed(self): self.interrupt(); self.policy.style=self.style.currentData()
    def toggle_watch(self):
        if self.policy.enabled: self.pause(); return
        if self.follow.currentData()=='locked' and not self.windows.currentData(): self.status('请先选择锁定游戏窗口'); return
        if not self.session: self.status('请先连接本地服务'); return
        self.policy.set_enabled(True); self.watch.setText('暂停观察'); self.status('观察已显式开启 · 每5秒仅检查当前目标窗口'); self.observe()
    def pause(self):
        self.policy.set_enabled(False); self.watch.setText('开始观察'); self.interrupt(); self.status('已暂停观察，待处理画面与语音已清除')
    def interrupt(self):
        self.policy.invalidate(); self.pending_manual=None; self.speech.stop(); self.answer=''; self.inflight_frame=''; self.pet.state='idle'
        if self.worker: self.worker.cancel()
    def toggle_mute(self): self.mute.setChecked(not self.mute.isChecked())
    def mute_changed(self,value):
        self.speech.muted=value; self.interrupt(); self.status('已静音，旧回答与待处理画面已清除；后续仅字幕' if value else '已恢复语音（以可用中文引擎为准）')
    def speaking(self,value): self.pet.state='speaking' if value else ('thinking' if self.worker else 'idle')
    def capture(self):
        if not self.target_valid or not self.selected: return None,'前台目标不可观察'
        region,reason=self.source.region(self.selected)
        if not region: return None,reason
        rect=QRect(*region)
        monitor=self.source.monitor(self.selected) if hasattr(self.source,'monitor') else None
        if monitor:
            name,physical=monitor
            screens=[s for s in QGuiApplication.screens() if s.name()==name]
            physical_rect=QRect(*physical)
            if len(screens)!=1 or not physical_rect.contains(rect):
                return None,'窗口跨屏或超出屏幕，请移入单个屏幕'
            screen=screens[0]
            # Win32 rectangles use physical pixels; Qt grabWindow uses monitor-local logical pixels.
            ratio=screen.devicePixelRatio()
            x=round((rect.x()-physical_rect.x())/ratio)
            y=round((rect.y()-physical_rect.y())/ratio)
            width=round(rect.width()/ratio); height=round(rect.height()/ratio)
        else:
            screens=[s for s in QGuiApplication.screens() if s.geometry().intersects(rect)]
            if len(screens)!=1 or not screens[0].geometry().contains(rect):
                return None,'窗口跨屏或超出屏幕，请移入单个屏幕'
            screen=screens[0]; origin=screen.geometry().topLeft()
            x,y,width,height=rect.x()-origin.x(),rect.y()-origin.y(),rect.width(),rect.height()
        pix=screen.grabWindow(0,x,y,width,height)
        # Recheck focus after capture, discard immediately if it changed.
        after,_=self.source.region(self.selected)
        if after!=region: return None,'窗口状态变化，本帧已丢弃'
        if pix.isNull(): return None,'无法采集窗口（独占全屏 / 保护窗口可能不支持）'
        image=pix.toImage().scaled(960,960,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        tiny=image.scaled(32,18).convertToFormat(image.Format.Format_RGB888)
        signature=bytes(tiny.constBits())
        if max(signature,default=0)-min(signature,default=0)<8: return None,'画面空白，等待可见游戏画面'
        data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer,'JPEG',72); buffer.close()
        return ('data:image/jpeg;base64,'+base64.b64encode(bytes(data)).decode(),signature),''
    def observe(self):
        if not self.policy.enabled or not self.refresh_target(): return
        if self.mode.currentData() in ('codex','openai') and not self.allow_images.isChecked():
            self.policy.latest=None; self.status('真实 provider 截图发送关闭；可进行文字聊天，勾选发送后才分析窗口'); return
        frame,reason=self.capture()
        if not frame:
            # Foreground loss cancels an in-flight response, too.
            if self.worker or self.policy.latest is not None or self.pet.state=='speaking': self.interrupt()
            self.status('观察等待：'+reason); return
        image,signature=frame
        if not self.policy.changed(signature):
            self.dispatch_latest(); return
        self.policy.new_frame(image)
        self.dispatch_latest()
    def dispatch_latest(self):
        if self.worker or self.pending_manual or self.pet.state=='speaking' or not self.policy.ready() or not self.policy.latest: return
        region,reason=self.source.region(self.selected)
        if not region: self.policy.latest=None; self.status('观察等待：'+reason); return
        image=self.policy.latest; self.policy.latest=None
        text=('主动观察当前办公或游戏目标窗口。只在可见证据显示重要变化、明确风险或值得提醒的信息时说一句简短中文，最多60字。'
              '没有重要信息、空白、重复提示或无法辨认时，仅返回 [[SILENT]]。不要编造动作、敌人、血量或未出现的事件。'
              +('可以有一句自然陪伴评论，但仍须有画面证据。' if self.personality.isChecked() else '只给事实和实用提醒，不闲聊。'))
        self.begin(text,image,True)
    def send_manual(self):
        text=self.prompt.text().strip()
        if not text or not self.session: return
        if self.policy.enabled: self.refresh_target()
        self.interrupt(); self.prompt.clear(); self.pending_manual=text
        if not self.worker: self.start_pending()
    def start_pending(self):
        if not self.pending_manual or self.worker: return
        text=self.pending_manual; self.pending_manual=None
        frame,_=self.capture() if self.policy.enabled and (self.mode.currentData() in ('demo','replay') or self.allow_images.isChecked()) else (None,'')
        self.begin(text,frame[0] if frame else '',False)
    def begin(self,text,image,automatic):
        self.turn+=1; self.auto=automatic; self.inflight_frame=image; self.answer=''; self.pet.state='thinking'
        generation=self.policy.generation
        self.append_log('[自动观察]' if automatic else '你：'+text)
        payload={'session':self.session,'turn':self.turn,'text':text,'image':image,'mode':self.mode.currentData(),'automatic':automatic,'allow_images':self.allow_images.isChecked(),**self.ctx()}
        self.worker=ApiWorker(self.url,self.token,payload,generation)
        self.worker.event.connect(self.on_event); self.worker.finished.connect(self.on_finished); self.worker.start()
        self.status('本地测试：非真正 AI，不理解画面' if self.mode.currentData()=='demo' else ('手工回放：非后台 AI' if self.mode.currentData()=='replay' else '正在请求 '+self.mode.currentText()+(' · 已发送目标截图' if image else ' · 仅文字')))
    def on_event(self,generation,kind,data):
        if generation!=self.policy.generation: return
        if kind=='delta': self.answer+=data.get('text','')
        elif kind=='done':
            if self.auto:
                region,reason=self.source.region(self.selected) if self.selected else (None,'窗口未选择')
                if not self.policy.enabled or not region:
                    self.interrupt(); self.status('观察等待：'+reason); return
            text=data.get('text',self.answer)
            if self.auto and not self.policy.accept_comment(text): self.status('没有新提醒，继续安静观察'); return
            if text.strip()=='[[SILENT]]': self.status('没有重要变化，继续观察'); return
            self.append_log(('星澜（测试 / 回放）：' if self.mode.currentData() in ('demo','replay') else '星澜：')+text)
            self.status(text); self.speech.say(text)
        elif kind=='error':
            message=data.get('error','请求失败')
            if self.auto and self.policy.enabled:
                region,_=self.source.region(self.selected) if self.selected else (None,'')
                if region and self.policy.retry(self.inflight_frame): message+='；已保留最新帧，稍后重试'
                else: message+='；自动重试已停止，等待新变化'
            self.append_log('提示：'+message); self.status(message)
        elif kind=='evidence': self.status('本轮读取 '+str(len(data))+' 条同领域 / 应用 / 任务 / 版本记忆')
        elif kind=='memory_saved': self.status(data.get('message','教学已保存')); self.load_memory()
    def on_finished(self,generation):
        # Worker is retained until its Python thread actually exits; no QThread destruction race.
        old=self.worker; self.worker=None; self.inflight_frame=''
        if old: old.deleteLater()
        if self.pet.state=='thinking': self.pet.state='idle'
        if self.pending_manual: QTimer.singleShot(200,self.start_pending)
        elif self.policy.enabled: QTimer.singleShot(250,self.dispatch_latest)
    def reset_session(self):
        self.interrupt()
        try: self.session=self.api('/api/session',{})['session']; self.log.clear(); self.status('新会话已建立，分层记忆保留')
        except Exception: self.status('新会话失败，请检查本地服务')
    def build_memory(self,tabs):
        panel=QWidget(); layout=QVBoxLayout(panel); tabs.addTab(panel,'分层记忆')
        self.tree=QTreeWidget(); self.tree.setHeaderLabels(['领域 / 应用或游戏 / 任务 / 版本 / 场景 / 问题','步骤与建议']); layout.addWidget(self.tree,1)
        self.tree.itemClicked.connect(self.edit_memory); self.memory_id=None; self.memory_fields={}
        form=QFormLayout()
        for key,label in [('steps','步骤'),('conditions','条件'),('advice','建议'),('source','来源')]:
            field=QLineEdit(); self.memory_fields[key]=field; form.addRow(label,field)
        layout.addLayout(form); layout.addWidget(QLabel('领域、应用 / 游戏、任务、版本、场景、问题使用聊天页当前字段；可纠正或忘记。'))
        guide_row=QHBoxLayout(); self.guide_url=QLineEdit(); self.guide_url.setPlaceholderText('公开攻略网址：https://…（版本由你明确填写）'); guide_row.addWidget(self.guide_url,1)
        self.import_button=QPushButton('教学导入网址正文'); self.import_button.clicked.connect(self.import_guide); guide_row.addWidget(self.import_button); layout.addLayout(guide_row)
        self.guide_info=QLabel('网址导入会抓取公开正文，按聊天页分支保存；网页内容需核对，无法保证攻略正确或适用当前版本。'); self.guide_info.setTextFormat(Qt.TextFormat.PlainText); self.guide_info.setWordWrap(True); layout.addWidget(self.guide_info)
        row=QHBoxLayout()
        for label,callback in [('保存教学',self.save_memory),('作为新教学',self.clear_memory),('忘记所选',self.delete_memory),('刷新',self.load_memory)]:
            button=QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        layout.addLayout(row)
    def render_memory(self,items):
        self.tree.clear(); groups={}
        for item in items:
            parent=self.tree.invisibleRootItem(); path=()
            hierarchy=[('办公' if item.get('scope')=='office' else '游戏'),item.get('application') if item.get('scope')=='office' else item['game'],item.get('task',''),item['version'],item['scene']]
            for value in hierarchy:
                path+=(value or '常规',)
                if path not in groups: groups[path]=QTreeWidgetItem(parent,[value or '常规'])
                parent=groups[path]
            leaf=QTreeWidgetItem(parent,[item['problem'],item['steps'] or item['advice']]); leaf.setData(0,Qt.ItemDataRole.UserRole,item)
        self.tree.expandAll()
    def edit_memory(self,item,*_):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data: return
        if data.get('scope','game')=='office' and data.get('application','')!=self.active_app:
            self.status('此教学属于其他办公应用；请切换到对应应用后编辑，避免混入当前分支'); return
        self.memory_id=data['id']; self.scope.setCurrentIndex(0 if data.get('scope')=='office' else 1)
        for key,field in self.fields.items(): field.setText(data.get(key,''))
        for key,field in self.memory_fields.items(): field.setText(data[key])
        self.guide_url.setText(data.get('source_url') or '')
        self.guide_info.setText(('来源：'+data.get('source_title','')+' · '+data.get('source_url','')+' · 抓取：'+time.strftime('%Y-%m-%d %H:%M',time.localtime(data['fetched_at']))) if data.get('fetched_at') else '手工教学：无网页抓取来源')
    def import_guide(self):
        if self.import_worker: self.status('攻略正在导入，请稍候'); return
        url=self.guide_url.text().strip(); ctx=self.ctx()
        if not url or not self.session: self.status('请填写公开攻略网址并连接服务'); return
        if not ctx['problem'] or (ctx['scope']=='game' and (not ctx['game'] or not ctx['version'])) or (ctx['scope']=='office' and not ctx['application']):
            self.status('导入前请明确游戏与版本或办公应用，以及关注问题；历史攻略请填历史版本'); return
        self.import_button.setEnabled(False); self.guide_info.setText('正在抓取公开正文并导入你明确选择的分支…')
        self.import_worker=GuideImportWorker(self.url,self.token,{'url':url,'explicit_teaching':True,**ctx},self.policy.generation)
        self.import_worker.result.connect(self.on_guide_import); self.import_worker.start()
    def on_guide_import(self,generation,ok,data):
        old=self.import_worker; self.import_worker=None; self.import_button.setEnabled(True)
        if old: old.deleteLater()
        if not ok: self.guide_info.setText(data.get('error','攻略导入失败')); return
        ctx=data['classification']; branch=(ctx['game']+' / '+ctx['version']) if ctx['scope']=='game' else ctx['application']+' / '+ctx['task']
        message='已将 '+data['title']+' 导入 '+branch+'：'+str(data['leaves'])+' 段。'+('仅索引受限片段；请检查记忆树。' if data.get('truncated') else '网页是参考资料，请核对版本与画面。')
        self.guide_info.setText(message+' 来源：'+data['url']); self.load_memory()
        if generation==self.policy.generation: self.status(message)
    def clear_memory(self):
        self.memory_id=None
        for field in self.memory_fields.values(): field.clear()
    def load_memory(self):
        try: self.render_memory(self.api('/api/state')['memories'])
        except Exception: self.status('无法刷新记忆，请检查服务')
    def save_memory(self):
        try:
            self.api('/api/memory',{'id':self.memory_id,**self.ctx(),**{k:f.text() for k,f in self.memory_fields.items()}})
            self.load_memory(); self.clear_memory(); self.status('教学已保存到当前领域 / 应用 / 任务 / 版本分支')
        except Exception: self.status('保存失败，请填写游戏与版本或办公应用，以及问题、步骤或建议')
    def delete_memory(self):
        if self.memory_id is None: return
        try: self.api('/api/memory/delete',{'id':self.memory_id}); self.load_memory(); self.clear_memory(); self.status('所选教学已忘记')
        except Exception: self.status('忘记失败，请检查服务')
    def closeEvent(self,event): event.ignore(); self.hide(); self.status('设置已收起，双击桌宠可重新打开')
    def shutdown(self):
        self.focus_timer.stop(); self.timer.stop(); self.policy.set_enabled(False); self.interrupt(); self.pet.timer.stop(); self.pet.close()
        if self.worker and self.worker.thread: self.worker.thread.join(timeout=2)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8793); args=parser.parse_args()
    app=QApplication.instance() or QApplication([]); app.setQuitOnLastWindowClosed(False)
    initialize_ui(app)
    # C03: no main window at startup. Settings and chat live behind the tray icon; the only
    # thing on screen is the pet itself. Double-clicking the pet or the tray reopens settings.
    companion=Companion(args.port)
    app.aboutToQuit.connect(companion.shutdown); app.exec()

if __name__=='__main__': main()
