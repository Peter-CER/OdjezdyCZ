"""What the board shows, as plain data, and the actions a user can take on it.

The GNOME Shell extension draws exactly this (over D-Bus), so all logic stays in Python.
No GTK imports.
"""

from __future__ import annotations

import time

from .alerts import BUFFER_CHOICES
from .models import REGION_NAMES, mode_symbol, mode_title, modes_list
from .providers import SIGN_UP_URL

BOARD_COLORS = [("auto", "Automatic"), ("white", "White"), ("grey", "Off-White"),
                ("dark", "Dark")]
TEXT_SIZES = [("small", "Small"), ("normal", "Normal"), ("large", "Large"),
              ("xlarge", "Extra Large")]
TEXT_SCALES = {"small": 0.9, "normal": 1.0, "large": 1.15, "xlarge": 1.3}


def _choice(config, key, choices) -> str:
    value = config.get(key)
    return value if any(value == c for c, _ in choices) else None


def board_color(config) -> str:
    return _choice(config, "boardColor", BOARD_COLORS) or "auto"


def text_size(config) -> str:
    return _choice(config, "textSize", TEXT_SIZES) or "normal"


def board_style(color: str, led: bool, system_dark: bool) -> str:
    """"dark", "white" or "grey" (off-white). Amber sign text needs the dark panel."""
    if led or color == "dark" or (color == "auto" and system_dark):
        return "dark"
    return "white" if color == "white" else "grey"


def distance_text(m: float) -> str:
    return f"{round(m / 10) * 10:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"


def hhmm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def time_text(d, now: float) -> tuple[str, bool]:
    """Countdown text and whether it's "now". Live second-precise predictions count down m:ss
    in the last 4 minutes; planned times stay in whole minutes."""
    secs = int((d.expected - now) // 1)
    mins = int(secs / 60)
    show_seconds = d.is_live and d.precise and secs < 240
    if (secs <= 0) if show_seconds else mins < 1:
        return "now", True
    if show_seconds:
        return f"{mins}:{secs % 60:02d}", False
    return (f"{mins} min" if mins < 60 else hhmm(d.expected)), False


def _icons(modes: int) -> str:
    return "".join(mode_symbol(m) for m in modes_list(modes))


def _join(*parts) -> str:
    return " · ".join(p for p in parts if p)


def _row(state, alert, g, d, now) -> dict:
    bg, fg = g.badge_colors(d)
    text, is_now = time_text(d, now)
    late = not d.is_cancelled and d.delay_minutes >= 2
    return {
        "id": d.id, "line": d.line, "bg": bg, "fg": fg, "destination": d.destination,
        "time": "Cancelled" if d.is_cancelled else text,
        "now": is_now and not d.is_cancelled,
        "live": d.is_live, "cancelled": d.is_cancelled,
        "planned": hhmm(d.planned) if late else "", "expected": hhmm(d.expected) if late else "",
        "dim": not d.is_cancelled and not g.is_catchable(d, now),
        "alert": alert.is_set(d), "favourite": state.is_favourite(d.line),
    }


def _message(state) -> dict:
    reason = {"denied": "Location access is off (Settings → Privacy & Security).",
              "rough": "Your location is only known roughly, too rough to pick a stop.",
              }.get(state.location_reason, "Couldn't determine your location.")
    search, retry = ["search", "Search Stops"], ["reload", "Try Again"]
    title, detail, buttons = {
        "no_location": ("No nearby stops", f"{reason} Search for a stop instead.",
                        [search, retry]),
        "outside_area": ("You're not in Czechia",
                         "Odjezdy shows departures for stops in Czechia.", [search]),
        "no_stops": ("No stops nearby", "", [search, retry]),
    }.get(state.phase, ("Finding nearby stops…", "", []))
    return {"title": title, "detail": detail, "buttons": buttons}


def _stop(state, alert, g, now) -> dict:
    sub = _join(g.town, distance_text(g.distance) if g.distance else "")
    if g.is_nearby and g.distance:
        sub += f" · 🚶 {g.walk_minutes} min"
    if state.location_source == "home":
        sub += " · from home"
    searched = bool(state.searched_group and state.searched_group.id == g.id)
    sections = [{
        "id": s.id,
        "title": ("★ YOUR LINES" if s.is_favourites
                  else f"{mode_symbol(s.mode)} {mode_title(s.mode).upper()}"),
        "more": ("Less ▴" if s.expanded else "More ▾") if s.has_more or s.expanded else "",
        "rows": [_row(state, alert, g, d, now) for d in s.departures],
    } for s in state.sections(now)]
    placeholder = ""
    if not sections:
        # "ok" without data means this one stop's request failed.
        placeholder = ("No departures soon" if g.id in state.departures
                       else "Loading departures…" if state.live == "loading"
                       else "Live data unavailable")
    p = alert.pending
    return {
        "name": g.name, "sub": sub, "icons": _icons(g.modes), "searched": searched,
        "tabs": [] if searched or len(state.groups) < 2 else [
            {"id": o.id, "label": f"{o.name} {distance_text(o.distance)}", "active": o.id == g.id}
            for o in state.groups],
        "notices": state.notices[:3],
        "alert": f"🔔 Alert at {hhmm(p.fire_at)} for {p.line} → {p.destination}" if p else "",
        "sections": sections, "placeholder": placeholder,
    }


def build_view(state, alert, config, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    view = {"led": bool(config.get("ledStyle")), "boardColor": board_color(config),
            "textSize": text_size(config), "search": None, "stop": None, "message": None}
    if state.search_open:
        short = len(state.search_text.strip()) < 2
        view["search"] = {
            "hint": f"Stop in {REGION_NAMES[state.search_region]}",
            "heading": "RECENT" if short and state.search_results else "",
            "empty": "" if state.search_results else
                     "Type a stop name." if short else "No stops found.",
            "results": [{"name": g.name, "icons": _icons(g.modes),
                         "sub": _join(g.town, distance_text(g.distance) if g.distance else "")}
                        for g in state.search_results],
        }
    elif state.phase == "ready" and state.selected:
        view["stop"] = _stop(state, alert, state.selected, now)
    else:
        view["message"] = _message(state)

    footer = ""
    if state.last_updated and state.phase == "ready":
        footer = ("⚠ Live data unavailable" if state.live == "unavailable"
                  else _join(f"Updated {hhmm(state.last_updated)}", state.data_source))
    view["footer"] = footer

    g = state.selected if state.phase == "ready" else None
    view["settings"] = {
        "led": view["led"], "lead": config.get("alertBufferMinutes") or 5,
        "boardColor": view["boardColor"], "boardColors": BOARD_COLORS,
        "textSize": view["textSize"], "textSizes": TEXT_SIZES,
        "leadChoices": BUFFER_CHOICES,
        "setHome": f"Use “{g.name}” as Home Location" if g else "",
        "hasHome": bool(config.get("homeLocation")),
        "hasKey": bool(config.golemio_key),      # the key itself is never sent back
        "signUpUrl": SIGN_UP_URL,
    }
    return view


def perform(state, alert, config, action: str, arg) -> None:
    """One user action. Unknown actions and out-of-date arguments are ignored."""
    g = state.selected

    def departure():
        return next((d for d in state.departures.get(g.id, []) if d.id == arg), None) if g else None

    if action == "reload":
        state.reload()
    elif action == "select" and any(o.id == arg for o in state.groups):
        state.select(arg)
    elif action == "expand":
        state.toggle_expanded(int(arg))
    elif action == "alert":
        d = departure()
        if d and not d.is_cancelled and state.region:
            alert.toggle(d, g, state.providers[state.region],
                         config.get("alertBufferMinutes") or 5)
    elif action == "cancelAlert":
        alert.cancel()
    elif action == "favourite":
        state.toggle_favourite(str(arg))
    elif action == "search":
        state.open_search()
    elif action == "searchText":
        if state.search_open and str(arg) != state.search_text:
            state.set_search_text(str(arg))
    elif action == "closeSearch":
        state.close_search()
    elif action == "choose" and isinstance(arg, int) and 0 <= arg < len(state.search_results):
        state.choose(state.search_results[arg])
    elif action == "nearby":
        state.clear_searched()
    elif action == "led":
        config.set("ledStyle", bool(arg))
    elif action == "boardColor" and any(arg == c for c, _ in BOARD_COLORS):
        config.set("boardColor", arg)
    elif action == "textSize" and any(arg == c for c, _ in TEXT_SIZES):
        config.set("textSize", arg)
    elif action == "lead" and arg in BUFFER_CHOICES:
        config.set("alertBufferMinutes", arg)
    elif action == "setHome" and g:
        state.set_home(g)
    elif action == "forgetHome":
        state.set_home(None)
    elif action == "key":
        state.set_golemio_key(str(arg))
