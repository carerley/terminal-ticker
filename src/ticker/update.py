from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .portfolio import state_directory


LATEST_RELEASE_API = "https://api.github.com/repos/carerley/terminal-ticker/releases/latest"
LATEST_TAG_API = "https://api.github.com/repos/carerley/terminal-ticker/tags?per_page=1"
RELEASE_NOTES_URL = "https://github.com/carerley/terminal-ticker/releases/latest"
BREW_FORMULA = "carerley/tap/ticker"
CHECK_INTERVAL = 24 * 60 * 60


@dataclass(frozen=True)
class Update:
    current: str
    latest: str
    release_url: str = RELEASE_NOTES_URL


def _version(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.removeprefix("v").split("."))
    except ValueError:
        return ()


def check_for_update(
    current: str,
    path: Path | None = None,
    opener: Callable[..., Any] = urlopen,
    now: int | None = None,
) -> Update | None:
    if os.environ.get("TICKER_NO_UPDATE_CHECK"):
        return None
    cache_path = path or state_directory() / "update.json"
    timestamp = int(time.time() if now is None else now)
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(cache_path.read_text())
    except (OSError, ValueError, TypeError):
        pass

    if timestamp - int(payload.get("checked_at", 0)) < CHECK_INTERVAL:
        return None
    try:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ticker-cli",
        }
        try:
            with opener(Request(LATEST_RELEASE_API, headers=headers), timeout=2) as response:
                release = json.loads(response.read())
            latest = str(release.get("tag_name", "")).removeprefix("v")
            release_url = str(release.get("html_url") or RELEASE_NOTES_URL)
        except HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            with opener(Request(LATEST_TAG_API, headers=headers), timeout=2) as response:
                tags = json.loads(response.read())
            latest = (
                str(tags[0].get("name", "")).removeprefix("v")
                if isinstance(tags, list) and tags and isinstance(tags[0], dict)
                else ""
            )
            release_url = (
                f"https://github.com/carerley/terminal-ticker/compare/"
                f"v{current}...v{latest}"
            )
        payload = {
            "checked_at": timestamp,
            "latest": latest,
            "release_url": release_url,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload) + "\n")
    except (OSError, ValueError, TypeError):
        return None

    if not _version(latest) or _version(latest) <= _version(current):
        return None
    return Update(current, latest, release_url)


def prompt_for_update(
    update: Update,
    read: Callable[[str], str] = input,
    run_command: Callable[..., Any] = subprocess.run,
) -> bool:
    print(f"\n  ✨ Update available! {update.current} -> {update.latest}\n")
    print(f"  Release notes: {update.release_url}\n")
    print(f"› 1. Update now (runs `brew upgrade {BREW_FORMULA}`)")
    print("  2. Skip")
    try:
        choice = read("\nChoose [1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if choice in {"", "1"}:
        try:
            result = run_command(["brew", "upgrade", BREW_FORMULA], check=False)
            if getattr(result, "returncode", 1) == 0:
                print("\nUpdate complete. Run `ticker` again.")
                return True
        except OSError as error:
            print(f"ticker: could not run Homebrew: {error}")
    return False
