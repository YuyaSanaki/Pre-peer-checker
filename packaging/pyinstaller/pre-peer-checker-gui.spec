# -*- mode: python ; coding: utf-8 -*-
"""DEPRECATED — primary distribution is local WebUI (install.sh). See docs/PACKAGING.md.

PyInstaller spec — GUI .app / onedir (legacy Mac packaging path).

Build on Apple Silicon Mac:
  pyinstaller packaging/pyinstaller/pre-peer-checker-gui.spec --noconfirm

Then wrap with scripts/build_macos.sh (codesign + .dmg + notarize).
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
    "PyQt6",
):
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass

datas = []
datas += collect_data_files("pre_peer_checker")
datas += [
    (
        str(ROOT / "src/pre_peer_checker/resources/pubpeer_patterns.json"),
        "pre_peer_checker/resources",
    )
]

a = Analysis(
    [str(ROOT / "src/pre_peer_checker/gui/app.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hidden
    + [
        "pre_peer_checker.gui.app",
        "pre_peer_checker.gui.worker",
        "pre_peer_checker.cli",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
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
    name="PrePeerChecker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,  # macOS drag-drop onto .app
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
    name="PrePeerChecker",
)

# macOS .app bundle (no-op on Linux; PyInstaller skips BUNDLE off-Darwin)
app = BUNDLE(
    coll,
    name="PrePeerChecker.app",
    icon=None,
    bundle_identifier="com.example.prepeerchecker",
    info_plist={
        "CFBundleName": "Pre-peer-checker",
        "CFBundleDisplayName": "Pre-peer-checker",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "NSRequiresAquaSystemAppearance": False,
    },
)
