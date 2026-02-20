# shortcut.spec
# PyInstaller spec file — used by GitHub Actions to build the Windows .exe
# Do not run this on Mac; it is built automatically in the cloud.

import sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Collect tkinterdnd2 data files (required for drag-and-drop to work)
dnd_datas = collect_data_files('tkinterdnd2')

a = Analysis(
    ['shortcut.py'],
    pathex=[],
    binaries=[],
    datas=dnd_datas,
    hiddenimports=[
        'tkinterdnd2',
        'customtkinter',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Shortcut',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No black console window — runs as a clean GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='shortcut.ico',    # Your custom icon
    version=None,
)
