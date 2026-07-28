# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the Calendar app (macOS .app bundle).

Build:  pyinstaller --noconfirm Calendar.spec
Output: dist/Calendar.app
"""

import os

from PyInstaller.utils.hooks import collect_all

# Version stamped into the bundle; the release workflow sets CALENDAR_VERSION
# from the git tag so the .app version tracks the release.
_VERSION = os.environ.get("CALENDAR_VERSION", "0.1.0")

# Bundle our runtime assets (icon.png resolved via sys._MEIPASS at runtime).
datas = [("assets", "assets")]
binaries = []
hiddenimports = []

# kerykeion ships ephemeris data (sweph/*.se1) and settings that must travel
# with the frozen app; swisseph (pyswisseph) is a compiled extension.
for pkg in ("kerykeion", "swisseph"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Calendar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/AppIcon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Calendar",
)

app = BUNDLE(
    coll,
    name="Calendar.app",
    icon="assets/AppIcon.icns",
    bundle_identifier="com.danielhaden.calendar",
    info_plist={
        "CFBundleName": "Calendar",
        "CFBundleDisplayName": "Calendar",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
