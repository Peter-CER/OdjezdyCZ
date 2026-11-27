"""Settings and remembered choices (the Mac app's UserDefaults), as one JSON file in
~/.config/odjezdy/config.json. Mode 600 because it can hold the Golemio API key."""

from __future__ import annotations

import json
import os
import threading


def default_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "odjezdy", "config.json")


class Config:
    def __init__(self, path: str | None = None):
        self.path = path or default_path()
        self._lock = threading.Lock()
        self._data: dict = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
        except (OSError, ValueError):
            pass

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        """Store (None removes) and write to disk atomically."""
        with self._lock:
            if value is None:
                self._data.pop(key, None)
            else:
                self._data[key] = value
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass  # settings just won't survive a restart

    @property
    def golemio_key(self) -> str | None:
        return (self.get("golemioKey") or "").strip() or None
