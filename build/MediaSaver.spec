# -*- mode: python ; coding: utf-8 -*-
# One-file, windowed PyInstaller build of Media Saver. Run through build/build.ps1 (see docs/BUILD.md).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))
from app import __version__  # noqa: E402

UI_DIR = ROOT / "ui"
ICON = ROOT / "build" / "icon.ico"
if not (UI_DIR / "index.html").is_file():
    raise SystemExit(f"ui/index.html not found in {UI_DIR}: the UI files are required for the build")

# --- Windows version resource ------------------------------------------------------------------------------------
_parts = (tuple(int(p) for p in __version__.split(".")) + (0, 0, 0, 0))[:4]
_version_file = ROOT / "build" / "_version_info.txt"
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_parts}, prodvers={_parts}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Media Saver'),
      StringStruct('FileDescription', 'Media Saver'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'MediaSaver'),
      StringStruct('LegalCopyright', ''),
      StringStruct('OriginalFilename', 'MediaSaver.exe'),
      StringStruct('ProductName', 'Media Saver'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

# --- bundle contents ---------------------------------------------------------------------------------------------
datas = [(str(UI_DIR), "ui")]
if ICON.is_file():
    datas.append((str(ICON), "."))  # main.py passes it to pywebview as the window icon

# Every app module (core included): the engine is imported lazily from main.py, so static analysis alone could miss it.
hiddenimports = collect_submodules("app") + [
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr_loader",
]

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "test", "idlelib", "lib2to3",
    "numpy", "PIL", "matplotlib", "scipy", "pandas", "IPython", "pytest", "setuptools", "pip", "wheel",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi", "cefpython3", "bottle_ssl",
]

a = Analysis(
    [str(ROOT / "build" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MediaSaver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger antivirus false positives
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(_version_file),
)
