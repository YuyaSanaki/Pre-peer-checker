# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — CLI only (Linux smoke / Mac CLI helper).

Build:
  pyinstaller packaging/pyinstaller/pre-peer-checker-cli.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH).resolve().parents[1]

hidden = []
for pkg in (
    "pre_peer_checker",
    "pymupdf",
    "fitz",
    "openpyxl",
    "docx",
    "jinja2",
    "nbformat",
    "ruamel.yaml",
    "scipy",
    "statsmodels",
    "PIL",
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

datas = []
datas += collect_data_files("pre_peer_checker")
# bundled pattern registry
datas += [
    (
        str(ROOT / "src/pre_peer_checker/resources/pubpeer_patterns.json"),
        "pre_peer_checker/resources",
    )
]

a = Analysis(
    [str(ROOT / "src/pre_peer_checker/cli.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden
    + [
        "pre_peer_checker.cli",
        "pre_peer_checker.pipeline.orchestrator",
        "pymupdf",
        "fitz",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "tensorflow",
        "mlx",
        "mlx_lm",
        "PyQt6",
        "PyQt5",
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pre-peer-checker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pre-peer-checker",
)
