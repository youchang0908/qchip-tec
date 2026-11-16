"""Native Windows WebView2 shell. No instrument API is exposed through a JS bridge."""
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import threading

from server import ASSET_ROOT


class DesktopInstance:
    """One desktop window per local service port; a second launch requests show."""
    def __init__(self, port):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in (
            ('CreateMutexW', [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('CreateEventW', [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.event = self.kernel.CreateEventW(None, False, False, 'Local\\QchipTEC.Show.{}'.format(port))
        if not self.event:
            raise ctypes.WinError(ctypes.get_last_error())
        self.mutex = self.kernel.CreateMutexW(None, False, 'Local\\QchipTEC.Desktop.{}'.format(port))
        code = ctypes.get_last_error()
        if not self.mutex:
            self.kernel.CloseHandle(self.event)
            raise ctypes.WinError(code)
        self.primary = code != 183  # ERROR_ALREADY_EXISTS

    def request_show(self):
        if not self.kernel.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_show(self):
        return self.kernel.WaitForSingleObject(self.event, 250) == 0

    def close(self):
        self.kernel.CloseHandle(self.mutex)
        self.kernel.CloseHandle(self.event)


def run_window(args, service_factory, check_service):
    instance = DesktopInstance(args.port)
    service = service_factory(args)
    quitting = threading.Event()
    tray_ready = threading.Event()
    tray = None
    watcher = None
    try:
        if not instance.primary:
            # Check mode when available; during primary startup it may not serve yet.
            check_service(service.url, args.device)
            instance.request_show()
            logging.info('Requested existing desktop window to show')
            return
        import webview
        import pystray
        from PIL import Image

        # Fail before starting a new acquisition service if the required renderer is missing.
        from webview.guilib import initialize
        gui = initialize('edgechromium')
        if gui.renderer != 'edgechromium':
            raise RuntimeError('需要 Microsoft Edge WebView2 Runtime 才能打开独立窗口。')

        service.start()
        webview.settings['ALLOW_FILE_URLS'] = False
        # Explicit native log-page CSV exports use authenticated local fetch + Blob.
        webview.settings['ALLOW_DOWNLOADS'] = True
        title = getattr(args, 'window_title', None) or 'Qchip TEC' + (' · 模拟' if args.device == 'simulation' else '')
        window = webview.create_window(title, service.url, width=1360, height=860,
            min_size=(420, 360), hidden=args.hidden, background_color='#F2F0E7', text_select=True)

        def show(_icon=None, _item=None):
            if not quitting.is_set():
                window.show()
                window.restore()
                logging.info('Existing desktop window restored')

        def closing():
            if quitting.is_set():
                return True
            # Closing callbacks block WinForms. Marshal hide after returning, not from
            # inside the callback, otherwise Invoke would wait on the blocked UI thread.
            threading.Thread(target=window.hide, daemon=True).start()
            logging.info('Window hidden to tray; service and recording continue')
            return False

        def quit_app(_icon=None, _item=None):
            if quitting.is_set():
                return
            count = service.recording_count()
            if count:
                answer = ctypes.windll.user32.MessageBoxW(None,
                    '当前有 {} 个通道正在录制。退出将结束采集与录制；TEC 输出保持原状态。\n确定退出？'.format(count),
                    '退出 Qchip TEC', 0x24 | 0x100)
                if answer != 6:
                    return
            quitting.set()
            window.destroy()

        window.events.closing += closing
        def quit_for_update():
            quitting.set()
            window.destroy()
        args._quit_for_update=quit_for_update
        window.events.loaded += lambda: logging.info('Desktop panel loaded (edgechromium)')
        icon_file = Image.open(ASSET_ROOT / 'packaging' / 'qchip.ico')
        tray_pixels = ctypes.windll.user32.GetSystemMetrics(49) or 16
        sizes = sorted(n for n, h in icon_file.ico.sizes() if n == h)
        tray_size = next((n for n in sizes if n >= tray_pixels), sizes[-1])
        tray_image = icon_file.ico.getimage((tray_size, tray_size)).copy()
        icon_file.close()
        tray = pystray.Icon('QchipTEC', tray_image, title,
            menu=pystray.Menu(pystray.MenuItem('打开面板', show, default=True), pystray.Menu.SEPARATOR,
                pystray.MenuItem('退出程序' if service.owned else '退出桌面窗口', quit_app)))

        def tray_setup(icon):
            icon.visible = True
            tray_ready.set()

        threading.Thread(target=lambda: tray.run(setup=tray_setup), name='Windows tray', daemon=True).start()
        if not tray_ready.wait(5):
            raise RuntimeError('托盘初始化失败，请重新启动应用。')

        def watch():
            while not quitting.is_set():
                if service.owned and service.done.is_set():
                    quitting.set()
                    window.destroy()
                    return
                if instance.wait_show():
                    show()

        def start_watch():
            nonlocal watcher
            watcher = threading.Thread(target=watch, name='Desktop activation', daemon=True)
            watcher.start()

        # Uses existing local HTTP APIs. No js_api or privileged browser bridge.
        # Browser profile/cache stays outside the user's cloud-synced project.
        profile = Path(os.environ['LOCALAPPDATA']) / 'QchipTEC' / ('webview2-' + str(args.port))
        webview.start(start_watch, gui='edgechromium', debug=False, private_mode=True,
            storage_path=str(profile),
            icon=str(ASSET_ROOT / 'packaging' / 'qchip.ico'))
    finally:
        quitting.set()
        if watcher is not None:
            watcher.join(timeout=2)
        if tray is not None:
            tray.stop()
        service.stop()  # Attached source/older services are deliberately left running.
        instance.close()
