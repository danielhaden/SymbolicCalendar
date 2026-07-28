"""Entry point for the calendar desktop app."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui import MainWindow
from ui.theme import ThemeManager, global_stylesheet


__version__ = "1.0.0"  # dev fallback; a packaged build reports its bundle version


def _asset(name: str) -> str:
    """Path to a bundled asset, working both from source and from a PyInstaller
    build (which unpacks data files under ``sys._MEIPASS``)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / "assets" / name)


def app_version() -> str:
    """The running version: the macOS bundle's CFBundleShortVersionString when
    packaged (kept in sync with the release tag), else the source version."""
    if getattr(sys, "frozen", False):
        try:
            import plistlib

            info = Path(sys.executable).resolve().parents[1] / "Info.plist"
            with open(info, "rb") as handle:
                version = plistlib.load(handle).get("CFBundleShortVersionString")
            if version:
                return str(version)
        except Exception:
            pass
    return __version__


def _selftest() -> int:
    """Headless smoke test: confirm the astronomy stack works (used to verify
    a packaged build actually computes moon data). Run: ``main.py --selftest``."""
    import datetime
    from model import (current_location, daylight, moon_aspects, moon_phase,
                       moonlight)

    day, loc = datetime.date.today(), current_location()
    lp, dl, ml = moon_phase(day), daylight(day), moonlight(day)
    aspects = moon_aspects(day, loc)
    print(f"selftest: phase={lp.phase_name if lp else None!r} "
          f"daylight={'ok' if dl else None} "
          f"moon_intervals={len(ml.intervals) if ml else None} "
          f"aspects={len(aspects)}")
    ok = lp is not None and dl is not None and ml is not None
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _check_update() -> int:
    """Diagnostic: confirm the app can reach GitHub and report any newer
    release. Useful for verifying update connectivity (incl. TLS in a build)."""
    import urllib.error
    import urllib.request

    from model.updates import GITHUB_REPO, check_for_update

    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:
            print(f"check-update: reached GitHub (HTTP {resp.status}) — TLS OK")
    except urllib.error.HTTPError as exc:
        print(f"check-update: reached GitHub (HTTP {exc.code}) — TLS OK "
              f"(likely no release yet)")
    except Exception as exc:
        print(f"check-update: FAILED to reach GitHub: {type(exc).__name__}: {exc}")
        return 1
    print(f"check-update: running version {app_version()}; "
          f"update available = {check_for_update(app_version())}")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    if "--check-update" in sys.argv:
        return _check_update()

    app = QApplication(sys.argv)
    app.setApplicationName("Calendar")
    app.setWindowIcon(QIcon(_asset("icon.png")))

    theme = ThemeManager()

    def apply_global() -> None:
        app.setStyleSheet(global_stylesheet(theme.current))

    theme.theme_changed.connect(apply_global)
    apply_global()

    window = MainWindow(theme=theme, version=app_version())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
