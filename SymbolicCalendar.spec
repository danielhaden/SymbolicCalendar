# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Symbolic Calendar (macOS .app bundle).

Build:  pyinstaller --noconfirm SymbolicCalendar.spec
Output: dist/Symbolic Calendar.app
"""

import os

from PyInstaller.utils.hooks import collect_all

# Version stamped into the bundle; the release workflow sets
# SYMBOLIC_CALENDAR_VERSION from the git tag so the .app version tracks the
# release.
_VERSION = os.environ.get("SYMBOLIC_CALENDAR_VERSION", "1.0.0")

# Bundle our runtime assets (icon.png resolved via sys._MEIPASS at runtime).
datas = [("assets", "assets")]
binaries = []
hiddenimports = []

# kerykeion ships ephemeris data (sweph/*.se1) and settings that must travel
# with the frozen app; swisseph (pyswisseph) is a compiled extension; certifi
# ships the CA bundle (cacert.pem) the update check verifies GitHub against.
for pkg in ("kerykeion", "swisseph", "certifi"):
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
    name="SymbolicCalendar",
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
    name="SymbolicCalendar",
)

app = BUNDLE(
    coll,
    name="Symbolic Calendar.app",
    icon="assets/AppIcon.icns",
    bundle_identifier="com.danielhaden.symboliccalendar",
    info_plist={
        "CFBundleName": "Symbolic Calendar",
        "CFBundleDisplayName": "Symbolic Calendar",
        "CFBundleShortVersionString": _VERSION,
        "CFBundleVersion": _VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
