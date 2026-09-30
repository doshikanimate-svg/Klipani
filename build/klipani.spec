# PyInstaller spec for the KLIPANI backend.
# Build (from project root):  .venv/bin/pyinstaller build/klipani.spec
# Output: dist/klipani-backend/klipani-backend (+ KLIPANI_DATA env at runtime)

import os
import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    [os.path.join(ROOT, "backend", "run.py")],
    pathex=[os.path.join(ROOT, "backend")],
    binaries=[(ffmpeg_exe, "imageio_ffmpeg/binaries")],
    datas=[
        (os.path.join(ROOT, "backend", "prompts.json"), "."),
        *collect_data_files("faster_whisper"),
    ],
    hiddenimports=[
        "ctranslate2",
        "faster_whisper",
        "googleapiclient.discovery_cache",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="klipani-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="klipani-backend",
)
