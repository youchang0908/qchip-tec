# Explicit program assets only; never package user profiles, settings, or data.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules,copy_metadata
root=Path(os.environ['QCHIP_BUILD_SOURCE'])
a=Analysis([str(root/'qchip_entry.py')],pathex=[str(root)],binaries=[],
    datas=[(str(root/'dist'/n),'dist') for n in ('index.html','app.js','app.css')]
        +[(str(root/'hardware_gate_dist'),'hardware_gate_dist'),(str(root/'packaging/qchip.ico'),'packaging')]
        +copy_metadata('PyVISA')+copy_metadata('PyVISA-py')+copy_metadata('pywebview'),
    hiddenimports=collect_submodules('pyvisa',filter=lambda n:'.testsuite' not in n)
        +collect_submodules('pyvisa_py',filter=lambda n:'.testsuite' not in n)
        +['serial','pystray._win32','webview.platforms.winforms','webview.platforms.edgechromium'],
    hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['tkinter','matplotlib','numpy','pytest','PyQt5','PyQt6','PySide2','PySide6','cefpython3'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='Qchip TEC',console=False,upx=False,
        icon=str(root/'packaging/qchip.ico'))
