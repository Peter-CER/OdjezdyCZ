""""Leave now" alert for one departure. Mirrors LeaveAlert.swift.

macOS can schedule a notification for later; here a GLib timer ticks every few seconds
instead (which also copes with suspend, since it compares wall-clock times), re-checking the
departure once a minute so the alert follows live delays. Nothing runs when no alert is set.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from gi.repository import GLib

from .models import Departure, DepartureError, StopGroup

BUFFER_CHOICES = [1, 2, 3, 5, 10, 15]
TICK_SECONDS = 5
RECHECK_SECONDS = 60


@dataclass
class Pending:
    departure_id: str
    group: StopGroup
    line: str
    destination: str
    expected: float
    buffer_minutes: int

    @property
    def fire_at(self) -> float:
        """When there are `buffer_minutes` left beyond the walk."""
        return self.expected - (self.group.walk_seconds + self.buffer_minutes * 60)


def message(p: Pending, now: float) -> tuple[str, str]:
    """Notification title and body. If the alert was set later than the lead time, it fires
    at once and says what slack is left."""
    slack = min(p.buffer_minutes * 60, p.expected - now - p.group.walk_seconds)
    minutes = int(slack // 60)
    target = f"{p.line} → {p.destination}"
    title = (f"Leave within {minutes} min for {target}" if minutes >= 1
             else f"Leave now for {target}")
    body = (f"Departs {time.strftime('%H:%M', time.localtime(p.expected))} from {p.group.name}"
            f" · {p.group.walk_minutes} min walk")
    if minutes >= 1:
        body += " — that gets you there on time"
    elif slack < 0:
        body += " — you'll need to hurry"
    return title, body


class LeaveAlert:
    def __init__(self, notify, on_change, log):
        self.notify = notify          # notify(title, body)
        self.on_change = on_change
        self.log = log
        self.pending: Pending | None = None
        self._provider = None
        self._timer = None
        self._last_check = 0.0

    def is_set(self, d: Departure) -> bool:
        return self.pending is not None and self.pending.departure_id == d.id

    def toggle(self, d: Departure, group: StopGroup, provider, buffer_minutes: int) -> None:
        if self.is_set(d):
            self.cancel()
            return
        self.cancel()
        self.pending = Pending(d.id, group, d.line, d.destination, d.expected, buffer_minutes)
        self._provider = provider
        self._last_check = time.monotonic()
        self.log(f"leave alert: {d.line} → {d.destination},"
                 f" notify in {max(0, self.pending.fire_at - time.time()):.0f} s")
        if self._tick():
            self._timer = GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        self.on_change()

    def cancel(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        self.pending = None
        self.on_change()

    def _tick(self) -> bool:
        p = self.pending
        if p is None:
            self._timer = None
            return False
        now = time.time()
        if now >= p.fire_at:
            self.notify(*message(p, now))
            self.pending = None
            self._timer = None
            self.on_change()
            return False
        if time.monotonic() - self._last_check >= RECHECK_SECONDS and p.fire_at - now > 30:
            self._last_check = time.monotonic()
            threading.Thread(target=self._recheck, args=(p, self._provider), daemon=True).start()
        return True

    def _recheck(self, p: Pending, provider) -> None:
        try:
            board = provider.board(p.group)
        except DepartureError:
            return
        found = next((d for d in board.departures if d.id == p.departure_id), None)
        if found:
            GLib.idle_add(self._moved, p, found.expected)

    def _moved(self, p: Pending, expected: float):
        if self.pending is p and abs(expected - p.expected) >= 20:
            p.expected = expected
            self.on_change()
        return False
