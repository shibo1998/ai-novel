# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置（M0 检查单 §4.4：Windows 打包 exe 可运行）
# 构建：pip install pyinstaller && pyinstaller ainovel.spec
# 产物：dist/ai-novel/ai-novel.exe（prompts/ lint/ web/dist 随 exe 同目录分发，见 README）
import os

block_cipher = None

a = Analysis(
    ["run.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[],
    hiddenimports=["ainovel", "ainovel.api",
                   "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                   "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
                   "anyio._backends._asyncio"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-novel",
    debug=False,
    strip=False,
    upx=False,
    console=True,   # 保留控制台便于看日志
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="ai-novel")
