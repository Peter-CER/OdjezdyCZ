"""Where are we? Mirrors LocationService.swift, with fallbacks Linux needs.

GeoClue positions are often very rough (tens of km from an IP address), so a fix worse than
MAX_ACCURACY is rejected rather than used to pick stops. In order of priority:
  1. $ODJEZDY_LOCATION="lat,lon" (testing)
  2. a good GeoClue fix
  3. the "home location" the user saved
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import gi
from gi.repository import GLib

try:
    gi.require_version("Geoclue", "2.0")
    from gi.repository import Geoclue
except (ValueError, ImportError):
    Geoclue = None

from . import APP_ID

MAX_ACCURACY = 500        # metres
TIMEOUT_SECONDS = 15


@dataclass
class Fix:
    lat: float
    lon: float
    source: str           # "gps" / "home" / "debug"


def parse_coordinates(text) -> tuple[float, float] | None:
    try:
        lat, lon = (float(p) for p in str(text).split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


class LocationService:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._last: tuple[float, Fix] | None = None
        self._waiters: list = []
        self._timeout = None
        self._request = 0

    def current(self, callback, max_age: float = 120) -> None:
        """Calls callback(Fix | None, reason) on the main loop. reason explains a missing
        GeoClue fix: "denied", "rough" or "unavailable"."""
        debug = parse_coordinates(os.environ.get("ODJEZDY_LOCATION", ""))
        if debug:
            GLib.idle_add(lambda: callback(Fix(*debug, "debug"), None) and False)
            return
        if self._last and time.monotonic() - self._last[0] < max_age:
            fix = self._last[1]
            GLib.idle_add(lambda: callback(fix, None) and False)
            return
        self._waiters.append(callback)
        if len(self._waiters) > 1:
            return
        self._request += 1
        request = self._request
        if Geoclue is None:
            self._finish(request, None, "unavailable")
            return
        self._timeout = GLib.timeout_add_seconds(
            TIMEOUT_SECONDS, self._timed_out, request)
        Geoclue.Simple.new(APP_ID, Geoclue.AccuracyLevel.EXACT, None, self._done, request)

    def _timed_out(self, request):
        self._timeout = None
        self._finish(request, None, "unavailable")
        return False

    def _done(self, _source, result, request):
        try:
            location = Geoclue.Simple.new_finish(result).get_location()
            accuracy = location.get_property("accuracy")
            self.log(f"location update (±{accuracy:.0f} m)")
            if accuracy > MAX_ACCURACY:
                self._finish(request, None, "rough")
                return
            fix = Fix(location.get_property("latitude"), location.get_property("longitude"), "gps")
            self._finish(request, fix, None)
        except GLib.Error as e:
            self.log(f"location failed: {e.message}")
            denied = "AccessDenied" in e.message
            self._finish(request, None, "denied" if denied else "unavailable")

    def _finish(self, request, fix, reason):
        if request != self._request or not self._waiters:
            return        # late answer to a request that already timed out
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = None
        if fix:
            self._last = (time.monotonic(), fix)
        else:
            home = parse_coordinates(self.config.get("homeLocation") or "")
            if home:
                fix = Fix(*home, "home")
        waiters, self._waiters = self._waiters, []
        for callback in waiters:
            callback(fix, reason)
