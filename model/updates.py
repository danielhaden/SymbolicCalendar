"""Update checking against GitHub Releases (UI-agnostic).

Fetches the latest published release for the project's repo and decides whether
it is newer than the running version. Network-only; the UI decides how (and
whether) to surface the result. Never raises — a failed/offline check just
returns ``None``.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

# The repo whose releases we check (owner/name on github.com).
GITHUB_REPO = "danielhaden/SymbolicCalendar"

_API = "https://api.github.com/repos/{repo}/releases/latest"
_TIMEOUT = 6.0  # seconds; a launch-time check must fail fast when offline


@dataclass(frozen=True)
class Release:
    """A published release the user could upgrade to."""

    version: str               # normalized, e.g. "0.2.0"
    tag: str                   # raw tag, e.g. "v0.2.0"
    url: str                   # release page (html_url)
    download_url: str | None   # the .dmg asset, when present


def _parts(version: str) -> tuple[int, ...]:
    """Numeric release components of a version string, ignoring any pre-release
    suffix. 'v0.2.0' -> (0, 2, 0); '0.0.0-abc123' -> (0, 0, 0)."""
    core = version.strip().lstrip("vV").split("-")[0].split("+")[0]
    out = []
    for piece in core.split("."):
        try:
            out.append(int(piece))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """Whether ``candidate`` is a strictly higher version than ``current``."""
    return _parts(candidate) > _parts(current)


def latest_release(repo: str = GITHUB_REPO) -> Release | None:
    """The repo's latest published release, or None if it can't be fetched."""
    try:
        request = urllib.request.Request(
            _API.format(repo=repo),
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "SymbolicCalendar-update-check",
            },
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            data = json.load(resp)
    except Exception:
        return None

    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    download_url = None
    for asset in data.get("assets") or []:
        if str(asset.get("name") or "").endswith(".dmg"):
            download_url = asset.get("browser_download_url")
            break
    return Release(
        version=tag.lstrip("vV"),
        tag=tag,
        url=str(data.get("html_url") or f"https://github.com/{repo}/releases"),
        download_url=download_url,
    )


def check_for_update(current: str, repo: str = GITHUB_REPO) -> Release | None:
    """The latest release if it is newer than ``current``, else None."""
    release = latest_release(repo)
    if release is not None and is_newer(release.version, current):
        return release
    return None
