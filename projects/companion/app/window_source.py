"""Foreground-only Windows capture boundary. No full-desktop capture fallback."""
import ctypes, os
from ctypes import wintypes
from pathlib import Path
from .observation import WindowInfo

DEFAULT_EXCLUSIONS = ['password','密码','passcode','one-time code','验证码','credential','凭据','payment','支付','付款','银行','banking','bank login','信用卡','credit card','1password','keepass','bitwarden','windows security','windows 安全','身份验证','认证','登录','sign in','log in','login']

class WindowsSource:
    def __init__(self):
        self.exclusions=list(DEFAULT_EXCLUSIONS)
        self.user32=ctypes.WinDLL('user32',use_last_error=True) if os.name=='nt' else None
        if self.user32:
            self.user32.GetForegroundWindow.restype=wintypes.HWND
            self.user32.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD]
            self.user32.MonitorFromWindow.restype=wintypes.HANDLE
            signatures={'IsWindow':[wintypes.HWND],'IsWindowVisible':[wintypes.HWND],'IsIconic':[wintypes.HWND],
                'GetWindowRect':[wintypes.HWND,ctypes.POINTER(wintypes.RECT)],'GetWindowTextLengthW':[wintypes.HWND],
                'GetWindowTextW':[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],
                'GetClassNameW':[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],
                'GetWindowThreadProcessId':[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]}
            for name,args in signatures.items(): getattr(self.user32,name).argtypes=args

    def application(self,pid):
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle: return 'pid:'+str(pid)
            try:
                length=wintypes.DWORD(32768); text=ctypes.create_unicode_buffer(length.value)
                if kernel.QueryFullProcessImageNameW(handle,0,text,ctypes.byref(length)): return Path(text.value).name.lower()
            finally: kernel.CloseHandle(handle)
        except (OSError,AttributeError): pass
        return 'pid:'+str(pid)

    def unlocked(self):
        if not self.user32: return False
        try:
            self.user32.OpenInputDesktop.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; self.user32.OpenInputDesktop.restype=wintypes.HANDLE
            self.user32.CloseDesktop.argtypes=[wintypes.HANDLE]
            self.user32.GetUserObjectInformationW.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
            desktop=self.user32.OpenInputDesktop(0,False,1)
            if not desktop: return False
            try:
                text=ctypes.create_unicode_buffer(256); needed=wintypes.DWORD()
                return bool(self.user32.GetUserObjectInformationW(desktop,2,text,ctypes.sizeof(text),ctypes.byref(needed))) and text.value.lower()=='default'
            finally: self.user32.CloseDesktop(desktop)
        except (OSError,AttributeError): return False

    def info(self,hwnd):
        if not self.user32 or not hwnd or not self.user32.IsWindow(hwnd): return None
        n=self.user32.GetWindowTextLengthW(hwnd); title=ctypes.create_unicode_buffer(n+1)
        self.user32.GetWindowTextW(hwnd,title,n+1); pid=wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return WindowInfo(int(hwnd),title.value,pid.value,self.application(pid.value))

    def eligibility(self,item):
        if not item: return '没有可观察的前台窗口'
        if item.process_id==os.getpid(): return '桌宠 / 设置窗口在前台，观察暂停'
        if not self.user32.IsWindowVisible(item.handle): return '窗口不可见'
        if self.user32.IsIconic(item.handle): return '窗口已最小化'
        cls=ctypes.create_unicode_buffer(256); self.user32.GetClassNameW(item.handle,cls,256)
        if cls.value in ('Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'): return '桌面 / 任务栏不可观察'
        if not item.title.strip(): return '无标题窗口不可观察'
        haystack=(item.title+' '+item.application).casefold()
        if any(word.strip().casefold() in haystack for word in self.exclusions if word.strip()): return '敏感标题 / 排除应用，观察暂停'
        return ''

    def foreground(self):
        if not self.user32: return None,'仅支持 Windows 窗口观察'
        if not self.unlocked(): return None,'锁屏 / 安全桌面不可观察'
        item=self.info(self.user32.GetForegroundWindow())
        reason=self.eligibility(item)
        return (None,reason) if reason else (item,'')

    def windows(self):
        if not self.user32: return []
        items=[]; callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        def visit(hwnd,_):
            item=self.info(hwnd)
            if not self.eligibility(item): items.append(item)
            return True
        self.user32.EnumWindows(callback(visit),0)
        return items

    def region(self,window):
        if not window: return None,'尚无目标窗口'
        item,reason=self.foreground()
        if not item: return None,reason
        if (item.handle,item.process_id)!=(window.handle,window.process_id): return None,'等待目标窗口回到前台'
        # Process-image identity is re-read before and after capture via region().
        # A transient lookup failure returns pid:<id>, which must not reuse an old app scope.
        if item.application!=window.application: return None,'应用身份已变化，等待重新确认前台'
        # Title changes can signal a new document or sensitive popup. Reject stale target.
        if item.title!=window.title: return None,'窗口标题已变化，等待重新确认前台'
        rect=wintypes.RECT()
        if not self.user32.GetWindowRect(item.handle,ctypes.byref(rect)): return None,'无法读取窗口区域'
        try:
            dwm=ctypes.WinDLL('dwmapi'); dwm.DwmGetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
            visible=wintypes.RECT()
            if dwm.DwmGetWindowAttribute(item.handle,9,ctypes.byref(visible),ctypes.sizeof(visible))==0: rect=visible
        except (OSError,AttributeError): pass
        if rect.right<=rect.left or rect.bottom<=rect.top: return None,'窗口区域无效'
        return (rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top),''

    def monitor(self,window):
        if not self.user32: return None
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD),('szDevice',wintypes.WCHAR*32)]
        info=MonitorInfo(); info.cbSize=ctypes.sizeof(info); monitor=self.user32.MonitorFromWindow(window.handle,0)
        self.user32.GetMonitorInfoW.argtypes=[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
        if not monitor or not self.user32.GetMonitorInfoW(monitor,ctypes.byref(info)): return None
        rect=info.rcMonitor
        return info.szDevice,(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top)

    def exclude_from_capture(self,handle):
        if not self.user32: return False
        try:
            self.user32.SetWindowDisplayAffinity.argtypes=[wintypes.HWND,wintypes.DWORD]
            return bool(self.user32.SetWindowDisplayAffinity(handle,0x11))
        except (AttributeError,OSError): return False
