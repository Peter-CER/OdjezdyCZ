"""The departure board window. Mirrors PopoverView.swift and DeparturesView.swift.

The whole content is rebuilt on each state change and once a second while open (for the
countdowns); it is a few dozen widgets, so that is cheap and keeps the code simple. The
search entry is the exception: it persists so typing isn't interrupted.
"""

from __future__ import annotations

import re
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import autostart  # noqa: E402
from .alerts import BUFFER_CHOICES  # noqa: E402
from .models import REGION_NAMES, mode_symbol, mode_title, modes_list  # noqa: E402
from .providers import SIGN_UP_URL  # noqa: E402
from .view import (BOARD_COLORS, TEXT_SCALES, TEXT_SIZES, board_color, board_style,  # noqa: E402
                   distance_text, hhmm, text_size, time_text)

WIDTH = 380

# The departure board; its pt sizes are scaled by the Text Size setting.
BOARD_CSS = """.board { background: #12141A; border-radius: 12px; padding: 6px 0; font-size: 11pt; }
.board label { color: #FFFFFF; }
.board button { background: none; border: none; box-shadow: none; padding: 0; min-height: 0; }
.board button:hover { background: rgba(255,255,255,0.06); }
.board .section { font-size: 8pt; font-weight: bold; color: rgba(255,255,255,0.45); }
.board .destination { font-weight: 500; }
.board .time { font-weight: bold; font-size: 11pt; }
.board .small { font-size: 8pt; }
.board .faded { color: rgba(255,255,255,0.4); }
.board .late { color: #FF9F0A; }
.board .now { color: #30D158; }
.board .red { color: #FF453A; }
.board .bell { color: #FFB000; }
.board.led .destination, .board.led .time, .board.led .now { color: #FFB000; font-family: monospace; }
.board.led .section { color: rgba(255,176,0,0.6); }
.board.light { background: #F2F2F2; border: 1px solid rgba(0,0,0,0.1); }
.board.light.white { background: #FFFFFF; }
.board.light label { color: #1D1D1F; }
.board.light button:hover { background: rgba(0,0,0,0.05); }
.board.light .section { color: rgba(0,0,0,0.5); }
.board.light .faded { color: rgba(0,0,0,0.4); }
.board.light .late { color: #A34A00; }
.board.light .now { color: #17702E; }
.board.light .red { color: #C01C28; }
.board.light .bell { color: #B27B00; }
.board label.badge { border-radius: 5px; padding: 2px 5px; font-weight: bold; font-size: 9pt; }
"""


def board_css(scale: float) -> bytes:
    return re.sub(r"(\d+)pt", lambda m: f"{int(m[1]) * scale:.1f}pt", BOARD_CSS).encode()


CSS = b"""
.dim { opacity: 0.4; }
.title { font-weight: bold; font-size: 12pt; }
.secondary { font-size: 9pt; opacity: 0.65; }
.tab { border-radius: 14px; padding: 2px 8px; font-size: 9pt; }
.notice { background: rgba(255,149,0,0.15); border-radius: 8px; padding: 8px; font-size: 9pt; }
.banner { background: alpha(currentColor, 0.07); border-radius: 8px; padding: 4px 8px; font-size: 9pt; }
"""


def label(text="", *classes, ellipsize=False, wrap=False, xalign=0.0):
    w = Gtk.Label(label=text, xalign=xalign)
    for c in classes:
        w.get_style_context().add_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_width_chars(1)
        w.set_max_width_chars(1)
        w.set_hexpand(True)
    if wrap:
        w.set_line_wrap(True)
        w.set_max_width_chars(40)
    return w


def box(*children, vertical=False, spacing=6, classes=()):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                spacing=spacing)
    for c in classes:
        b.get_style_context().add_class(c)
    for child in children:
        if child is not None:
            expand = not vertical and child.get_hexpand()
            b.pack_start(child, expand, expand, 0)
    return b


def button(content, on_click, tooltip=None, flat=True, classes=()):
    b = Gtk.Button()
    b.add(label(content, xalign=0.5) if isinstance(content, str) else content)
    if flat:
        b.set_relief(Gtk.ReliefStyle.NONE)
    for c in classes:
        b.get_style_context().add_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    b.connect("clicked", lambda *_: on_click())
    return b


class BoardWindow(Gtk.Window):
    def __init__(self, state, alert, config, quit_app):
        super().__init__(title="Odjezdy")
        self.state, self.alert, self.config, self.quit_app = state, alert, config, quit_app
        self.editing_key = False
        self._tick = None
        self._retry = None
        self._countdowns = None
        self._badge_css: dict[tuple[str, str], str] = {}
        # The board follows the desktop's light / dark style.
        self._interface = Gio.Settings.new("org.gnome.desktop.interface")
        self._interface.connect("changed::color-scheme",
                                lambda *_: self.get_visible() and self.render())

        self.set_default_size(WIDTH, -1)
        self.set_resizable(False)
        self.set_icon_name("odjezdy")
        self.set_position(Gtk.WindowPosition.CENTER)

        self._provider = Gtk.CssProvider()
        self._provider.load_from_data(CSS)
        self._badges = Gtk.CssProvider()
        self._board_provider = Gtk.CssProvider()
        self._scale = None
        for provider in (self._provider, self._board_provider, self._badges):
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.root = box(vertical=True, spacing=10)
        self.root.set_border_width(12)
        self.root.set_size_request(WIDTH - 24, -1)
        self.add(self.root)
        self.content = box(vertical=True, spacing=10)
        self.root.pack_start(self.content, False, False, 0)

        # Search panel (persistent entry).
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("changed", lambda e: self.state.set_search_text(e.get_text()))
        self.search_entry.connect("activate", lambda *_: self._choose_first())
        self.search_results = box(vertical=True, spacing=2)
        self.search_panel = box(
            box(self.search_entry, button("Cancel", self.state.close_search)),
            self.search_results, vertical=True, spacing=8)
        self.search_panel.show_all()
        self.search_panel.set_no_show_all(True)
        self.search_panel.hide()
        self.root.pack_start(self.search_panel, False, False, 0)

        self.footer = box()
        self.root.pack_start(self.footer, False, False, 0)
        self.root.show()

        self.connect("delete-event", lambda *_: self.hide() or True)  # close = hide to tray
        self.connect("key-press-event", self._key_press)
        self.connect("show", self._shown)
        self.connect("hide", self._hidden)

    # --- Open / close ---

    def _shown(self, *_):
        self.state.opened()
        if not self._tick:
            self._tick = GLib.timeout_add_seconds(1, self._second)

    def _hidden(self, *_):
        self.state.closed()
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = None

    def _second(self):
        # Only the countdowns change between state updates; leave menus and typing alone.
        # Redraw only when a visible countdown changed, so clicks and tooltips survive.
        if self.state.phase == "ready" and not self.state.search_open and not self.editing_key \
                and self._countdown_state() != self._countdowns:
            self.render()
        return True

    def _countdown_state(self):
        g, now = self.state.selected, time.time()
        if not g:
            return None
        return [(d.id, time_text(d, now)[0], g.is_catchable(d, now))
                for section in self.state.sections(now) for d in section.departures]

    def _retry_render(self):
        self._retry = None
        if self.get_visible():
            self.render()
        return False

    def _menu_open(self) -> bool:
        grabbed = Gtk.grab_get_current()
        return isinstance(grabbed, Gtk.Menu)

    def _key_press(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            if self.state.search_open:
                self.state.close_search()
            elif self.editing_key:
                self.editing_key = False
                self.render()
            else:
                self.hide()
            return True
        return False

    def show_search(self):
        self.editing_key = False
        self.search_entry.set_text("")
        self.state.open_search()
        self.search_entry.grab_focus()

    def _choose_first(self):
        if self.state.search_results:
            self.state.choose(self.state.search_results[0])

    # --- Rendering ---

    def render(self):
        s = self.state
        if self._menu_open():    # rebuilding would close it: try again shortly
            if not self._retry:
                self._retry = GLib.timeout_add(500, self._retry_render)
            return
        self._countdowns = self._countdown_state()
        scale = TEXT_SCALES[text_size(self.config)]
        if scale != self._scale:
            self._scale = scale
            self._board_provider.load_from_data(board_css(scale))
        for child in self.content.get_children():
            if not (self.editing_key and getattr(child, "is_key_entry", False)):
                child.destroy()
        for child in self.footer.get_children():
            child.destroy()

        searching = s.search_open and not self.editing_key
        self.search_panel.set_visible(searching)
        if searching:
            self._render_search_results()
        elif self.editing_key:
            if not self.content.get_children():
                self._add(self._key_entry())
        elif s.phase == "ready" and s.selected:
            self._render_stop(s.selected)
        else:
            self._add(self._message())
        self._render_footer()
        self.content.show_all()
        self.footer.show_all()
        self.resize(WIDTH, 1)   # shrink to fit

    def _add(self, widget):
        self.content.pack_start(widget, False, False, 0)

    def _message(self):
        s = self.state
        reason = {"denied": "Location access is off (Settings → Privacy & Security).",
                  "rough": "Your location is only known roughly, too rough to pick a stop.",
                  }.get(s.location_reason, "Couldn't determine your location.")
        title, detail, actions = {
            "no_location": ("No nearby stops", f"{reason} Search for a stop instead.",
                            [("Search Stops", self.show_search), ("Try Again", s.reload)]),
            "outside_area": ("You're not in Czechia",
                             "Odjezdy shows departures for stops in Czechia.",
                             [("Search Stops", self.show_search)]),
            "no_stops": ("No stops nearby", "",
                         [("Search Stops", self.show_search), ("Try Again", s.reload)]),
        }.get(s.phase, ("Finding nearby stops…", "", []))
        parts = []
        if s.phase in ("idle", "locating"):
            spinner = Gtk.Spinner()
            spinner.start()
            parts.append(spinner)
        parts.append(label(title, "title", xalign=0.5))
        if detail:
            text = label(detail, "secondary", wrap=True, xalign=0.5)
            text.set_justify(Gtk.Justification.CENTER)
            parts.append(text)
        if actions:
            row = box(*[button(t, f, flat=False) for t, f in actions])
            row.set_halign(Gtk.Align.CENTER)
            parts.append(row)
        out = box(*parts, vertical=True, spacing=8)
        out.set_margin_top(18)
        out.set_margin_bottom(18)
        return out

    def _render_stop(self, g):
        s = self.state
        sub = " · ".join(p for p in (g.town, distance_text(g.distance) if g.distance else "") if p)
        if g.is_nearby and g.distance:
            sub += f" · 🚶 {g.walk_minutes} min"
        if s.location_source == "home":
            sub += " · from home"
        name = box(label(g.name, "title", ellipsize=True), label(sub, "secondary", ellipsize=True),
                   vertical=True, spacing=2)
        name.set_hexpand(True)
        icons = label("".join(mode_symbol(m) for m in modes_list(g.modes)))
        self._add(box(name, icons, button("🔍", self.show_search,
                                          f"Find a stop in {REGION_NAMES[s.search_region]}")))

        if s.searched_group and s.searched_group.id == g.id:
            text = label("Searched stop", ellipsize=True)
            self._add(box(text, button("⌖ Back to nearby", s.clear_searched), classes=("banner",)))
        elif len(s.groups) > 1:
            tabs = []
            for other in s.groups:
                t = Gtk.ToggleButton()
                t.add(label(f"{other.name} {distance_text(other.distance)}", ellipsize=True))
                t.get_style_context().add_class("tab")
                t.set_tooltip_text(other.name)
                t.set_active(other.id == g.id)
                t.set_hexpand(True)
                t.connect("clicked", lambda _b, i=other.id: self._select(i))
                tabs.append(t)
            self._add(box(*tabs, spacing=4))

        if s.notices:
            items = []
            for n in s.notices[:3]:
                text = label("⚠ " + n, wrap=True)
                text.set_lines(3)
                text.set_ellipsize(Pango.EllipsizeMode.END)
                text.set_tooltip_text(n)
                items.append(text)
            self._add(box(*items, vertical=True, spacing=4, classes=("notice",)))

        p = self.alert.pending
        if p:
            text = label(f"🔔 Alert at {hhmm(p.fire_at)} for {p.line} → {p.destination}",
                         ellipsize=True)
            self._add(box(text, button("Cancel", self.alert.cancel), classes=("banner",)))

        self._add(self._board(g))

    def _select(self, group_id):
        if group_id == self.state.selected_id:
            self.render()       # re-press keeps the tab active
        else:
            self.state.select(group_id)

    def _board(self, g):
        s = self.state
        now = time.time()
        board = box(vertical=True, spacing=0, classes=("board",))
        led = bool(self.config.get("ledStyle"))
        style = board_style(board_color(self.config), led,
                            self._interface.get_string("color-scheme") == "prefer-dark")
        classes = ["led"] if led else [] if style == "dark" else ["light"]
        if style == "white":
            classes.append("white")
        for c in classes:
            board.get_style_context().add_class(c)
        sections = s.sections(now)
        if not sections:
            has_data = g.id in s.departures
            # "ok" without data means this one stop's request failed.
            text = ("No departures soon" if has_data
                    else "Loading departures…" if s.live == "loading"
                    else "Live data unavailable")
            placeholder = label(text, "faded", xalign=0.5)
            placeholder.set_size_request(-1, 64)
            board.pack_start(placeholder, False, False, 0)
            return board
        # One badge column for the whole board, as wide as its longest line name.
        self._badge_column = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        for section in sections:
            board.pack_start(self._section_header(section), False, False, 0)
            for d in section.departures:
                board.pack_start(self._row(g, d, now), False, False, 0)
        return board

    def _section_header(self, section):
        title = ("★ YOUR LINES" if section.is_favourites
                 else f"{mode_symbol(section.mode)} {mode_title(section.mode).upper()}")
        expandable = section.has_more or section.expanded
        more = label(("Less ▴" if section.expanded else "More ▾") if expandable else "",
                     "section", xalign=1.0)
        content = box(label(title, "section", ellipsize=True), more)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(4)
        if not expandable:
            return content
        return button(content, lambda: self.state.toggle_expanded(section.id),
                      "Show fewer" if section.expanded else "Show later departures")

    def _badge(self, g, d):
        bg, fg = g.badge_colors(d)
        name = self._badge_css.get((bg, fg))
        if not name:
            name = self._badge_css[(bg, fg)] = f"badge-{len(self._badge_css)}"
            css = "".join(f".board label.{n} {{ background: {b}; color: {f}; }}\n"
                          for (b, f), n in self._badge_css.items())
            self._badges.load_from_data(css.encode())
        w = label(d.line, "badge", name, xalign=0.5)
        w.set_size_request(34, 22)
        w.set_valign(Gtk.Align.CENTER)
        holder = box(w, spacing=0)
        holder.set_size_request(46, -1)
        self._badge_column.add_widget(holder)
        return holder

    def _row(self, g, d, now):
        destination = label(d.destination, "destination", ellipsize=True)
        if d.is_cancelled:
            destination.set_markup(f"<s>{GLib.markup_escape_text(d.destination)}</s>")
            destination.get_style_context().add_class("faded")

        right = box(vertical=True, spacing=0)
        top = box(spacing=4)
        top.set_halign(Gtk.Align.END)
        if d.is_cancelled:
            top.pack_start(label("Cancelled", "time", "red"), False, False, 0)
        else:
            if self.alert.is_set(d):
                top.pack_start(label("🔔", "bell", "small"), False, False, 0)
            if d.is_live:
                top.pack_start(label("●", "now", "small"), False, False, 0)
            text, is_now = time_text(d, now)
            top.pack_start(label(text, "time", *(["now"] if is_now else [])), False, False, 0)
        right.pack_start(top, False, False, 0)
        if not d.is_cancelled and d.delay_minutes >= 2:
            planned = label()
            planned.set_markup(f"<s>{hhmm(d.planned)}</s>")
            planned.get_style_context().add_class("faded")
            delay = box(planned, label(hhmm(d.expected), "late"), spacing=3,
                        classes=("small",))
            delay.set_halign(Gtk.Align.END)
            right.pack_start(delay, False, False, 0)

        content = box(self._badge(g, d), destination, right, spacing=10)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(5)
        content.set_margin_bottom(5)
        # Leaves before you can walk there: dimmed.
        if not d.is_cancelled and not g.is_catchable(d, now):
            content.get_style_context().add_class("dim")

        row = Gtk.EventBox()
        row.set_visible_window(False)
        row.add(content)
        row.set_tooltip_text(
            ("Live" if d.is_live else "Planned (no live data for this trip)")
            + (" · Leave-now alert set (click to cancel)" if self.alert.is_set(d)
               else " · Click for a leave-now alert, right-click for more"))
        row.connect("button-press-event", self._row_clicked, g, d)
        return row

    def _row_clicked(self, row, event, g, d):
        if event.button == 1 and not d.is_cancelled:
            self._toggle_alert(g, d)
        elif event.button == 3:
            menu = Gtk.Menu()
            if not d.is_cancelled:
                item = Gtk.MenuItem(label="Cancel Leave-Now Alert" if self.alert.is_set(d)
                                    else "Leave-Now Alert")
                item.connect("activate", lambda *_: self._toggle_alert(g, d))
                menu.append(item)
            line = d.line
            item = Gtk.MenuItem(label=f"Remove {line} from Your Lines"
                                if self.state.is_favourite(line)
                                else f"Add {line} to Your Lines")
            item.connect("activate", lambda *_: self.state.toggle_favourite(line))
            menu.append(item)
            menu.attach_to_widget(self, None)
            menu.show_all()
            menu.popup_at_pointer(event)
        return True

    def _toggle_alert(self, g, d):
        provider = self.state.providers[self.state.region]
        self.alert.toggle(d, g, provider, self.config.get("alertBufferMinutes") or 5)

    def _render_search_results(self):
        s = self.state
        self.search_entry.set_placeholder_text(f"Stop in {REGION_NAMES[s.search_region]}")
        for child in self.search_results.get_children():
            child.destroy()
        short = len(s.search_text.strip()) < 2
        if short and s.search_results:
            self.search_results.pack_start(label("RECENT", "secondary"), False, False, 0)
        if not s.search_results:
            empty = label("Type a stop name." if short else "No stops found.", "secondary",
                          xalign=0.5)
            empty.set_size_request(-1, 40)
            self.search_results.pack_start(empty, False, False, 0)
        for g in s.search_results:
            sub = " · ".join(p for p in (g.town, distance_text(g.distance) if g.distance else "")
                             if p)
            name = box(label(g.name, ellipsize=True), label(sub, "secondary", ellipsize=True),
                       vertical=True, spacing=1)
            name.set_hexpand(True)
            icons = label("".join(mode_symbol(m) for m in modes_list(g.modes)))
            row = button(box(name, icons), lambda g=g: s.choose(g))
            self.search_results.pack_start(row, False, False, 0)
        self.search_results.show_all()

    def _key_entry(self):
        entry = Gtk.Entry()
        entry.set_text(self.config.golemio_key or "")
        entry.set_placeholder_text("Paste key")
        entry.set_visibility(False)

        def close(save):
            self.editing_key = False
            if save:
                self.state.set_golemio_key(entry.get_text())
            self.render()

        entry.connect("activate", lambda *_: close(True))
        out = box(
            label("Golemio API key (Prague)", "title"),
            label("Optional. Without it Prague uses MOTIS; with it you get second-accurate "
                  "times, live buses and stop notices. Free at api.golemio.cz, stored only on "
                  "this computer.", "secondary", wrap=True),
            entry,
            box(Gtk.LinkButton.new_with_label(SIGN_UP_URL, "Get a key"),
                button("Cancel", lambda: close(False), flat=False),
                button("Save", lambda: close(True), flat=False)),
            vertical=True, spacing=8)
        out.is_key_entry = True
        return out

    def _render_footer(self):
        s = self.state
        text, tooltip = "", None
        if s.last_updated and s.phase == "ready":
            if s.live == "unavailable":
                text = "⚠ Live data unavailable"
            else:
                text = f"Updated {hhmm(s.last_updated)}" + (
                    f" · {s.data_source}" if s.data_source else "")
                tooltip = ("Data from motis.obehy.cz (to the minute)."
                           if s.data_source == "MOTIS"
                           else "Data from Golemio, Prague's open-data API")
        status = label(text, "secondary", ellipsize=True)
        if tooltip:
            status.set_tooltip_text(tooltip)
        menu_button = Gtk.MenuButton()
        menu_button.add(label("⚙"))
        menu_button.set_relief(Gtk.ReliefStyle.NONE)
        menu_button.set_popup(self._settings_menu())
        for w in (status, button("🔍", self.show_search, "Find a stop"),
                  button("⟳", s.reload, "Refresh"), menu_button):
            self.footer.pack_start(w, w.get_hexpand(), w.get_hexpand(), 0)

    def _settings_menu(self):
        s, config = self.state, self.config
        menu = Gtk.Menu()

        def add(item, handler=None, signal="activate"):
            if handler:
                item.connect(signal, handler)
            menu.append(item)
            return item

        login = Gtk.CheckMenuItem(label="Start at Login")
        login.set_active(autostart.is_enabled())
        add(login, lambda i: autostart.set_enabled(i.get_active()), "toggled")

        led = Gtk.CheckMenuItem(label="Sign-style Amber Text")
        led.set_active(bool(config.get("ledStyle")))
        add(led, lambda i: (config.set("ledStyle", i.get_active()), self.render()), "toggled")

        for title, key, choices, current in (
                ("Board Colour", "boardColor", BOARD_COLORS, board_color(config)),
                ("Text Size", "textSize", TEXT_SIZES, text_size(config))):
            submenu, first = Gtk.Menu(), None
            for value, text in choices:
                item = Gtk.RadioMenuItem(label=text, group=first)
                first = first or item
                item.set_active(value == current)
                item.connect("toggled", lambda i, k=key, v=value: i.get_active() and (
                    config.set(k, v), GLib.idle_add(lambda: self.render() and False)))
                submenu.append(item)
            add(Gtk.MenuItem(label=title)).set_submenu(submenu)

        lead = Gtk.Menu()
        group = None
        for minutes in BUFFER_CHOICES:
            item = Gtk.RadioMenuItem(label=f"{minutes} min before I must leave", group=group)
            group = group or item
            item.set_active(minutes == (config.get("alertBufferMinutes") or 5))
            item.connect("toggled", lambda i, m=minutes:
                         i.get_active() and config.set("alertBufferMinutes", m))
            lead.append(item)
        add(Gtk.MenuItem(label="Leave-Now Alert")).set_submenu(lead)

        add(Gtk.SeparatorMenuItem())
        g = s.selected if s.phase == "ready" else None
        if g:
            add(Gtk.MenuItem(label=f"Use “{g.name}” as Home Location"),
                lambda *_: s.set_home(g))
        if config.get("homeLocation"):
            add(Gtk.MenuItem(label="Forget Home Location"), lambda *_: s.set_home(None))

        def edit_key(*_):
            self.editing_key = True
            s.search_open = False
            GLib.idle_add(lambda: self.render() and False)

        add(Gtk.MenuItem(label="Golemio API Key (Prague, optional)…"), edit_key)
        add(Gtk.SeparatorMenuItem())
        add(Gtk.MenuItem(label="Quit Odjezdy"), lambda *_: self.quit_app())
        menu.show_all()
        return menu
