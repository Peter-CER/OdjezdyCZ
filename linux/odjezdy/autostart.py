"""Start at login via an XDG autostart entry. Mirrors LoginItem.swift, but opt-in."""

from __future__ import annotations

import os
import shutil
import sys

from . import APP_ID


def _path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", f"{APP_ID}.desktop")


def _command() -> str:
    installed = shutil.which("odjezdy")
    if installed:
        return f'"{installed}" --hidden'
    # Running from a checkout: point at its launcher script.
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    return f'"{sys.executable}" "{launcher}" --hidden'


def is_enabled() -> bool:
    return os.path.exists(_path())


def set_enabled(on: bool) -> None:
    try:
        if on:
            os.makedirs(os.path.dirname(_path()), exist_ok=True)
            with open(_path(), "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=Odjezdy\n"
                        f"Exec={_command()}\nIcon=odjezdy\nTerminal=false\n")
        elif os.path.exists(_path()):
            os.remove(_path())
    except OSError as e:
        print(f"odjezdy: login item change failed: {e}", file=sys.stderr)
