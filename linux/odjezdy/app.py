"""Tray icon, single instance, D-Bus service and notifications. Mirrors OdjezdyApp.swift.

Two front ends share one process: the GNOME Shell extension (a drop-down under the top bar,
talking D-Bus) and, where it isn't running, a tray icon with a window.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    AppIndicator = None

from . import APP_ID  # noqa: E402
from .alerts import LeaveAlert  # noqa: E402
from .config import Config  # noqa: E402
from .location import LocationService  # noqa: E402
from .service import BoardService  # noqa: E402
from .state import AppState  # noqa: E402
from .ui import BoardWindow  # noqa: E402


def log(message: str) -> None:
    if os.environ.get("ODJEZDY_DEBUG"):
        print(f"odjezdy: {message}", file=sys.stderr, flush=True)


def icon_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.normpath(os.path.join(here, "..", "data"))
    return local if os.path.isdir(local) else "/usr/share/icons/hicolor/scalable/apps"


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.indicator = None
        self.state = self.alert = self.config = None
        self.service = BoardService(self)
        self.attached = False             # the shell extension is the front end

    def do_dbus_register(self, connection, object_path):
        Gtk.Application.do_dbus_register(self, connection, object_path)
        self.service.export(connection)
        return True

    def do_dbus_unregister(self, connection, object_path):
        self.service.unexport()
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.IconTheme.get_default().append_search_path(icon_dir())
        config = Config()
        location = LocationService(config, log)
        state = AppState(config, location, log)
        alert = LeaveAlert(self.notify, self._changed, log)
        self.state, self.alert, self.config = state, alert, config
        self.window = BoardWindow(state, alert, config, self.quit)
        state.on_change = self._changed
        self.add_window(self.window)
        self.hold()                       # stay running with the window hidden

    def _changed(self):
        if self.window and self.window.get_visible():
            self.window.render()
        self.service.changed()

    def set_attached(self, attached: bool):
        """The extension took over (hide our own icon and window) or went away."""
        self.attached = attached
        if attached:
            self.window.hide()
        elif self.state.is_open and not self.window.get_visible():
            self.state.closed()
        if self.indicator:
            status = AppIndicator.IndicatorStatus
            self.indicator.set_status(status.PASSIVE if attached else status.ACTIVE)

    def set_board_open(self, is_open: bool):
        if is_open:
            self.state.opened()
        else:
            self.state.search_open = False
            self.state.closed()

    def _make_indicator(self):
        if AppIndicator is None:
            log("no AyatanaAppIndicator3: running without a tray icon")
            return
        indicator = AppIndicator.Indicator.new(
            APP_ID, "odjezdy-symbolic", AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        indicator.set_icon_theme_path(icon_dir())
        indicator.set_title("Odjezdy")
        menu = Gtk.Menu()
        for text, handler in (("Show Departures", self.show_board),
                              ("Search Stops…", self.show_search),
                              (None, None),
                              ("Quit Odjezdy", self.quit)):
            if text is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=text)
            item.connect("activate", lambda _i, h=handler: h())
            menu.append(item)
            if handler == self.show_board:
                indicator.set_secondary_activate_target(item)    # middle click
        menu.show_all()
        indicator.set_menu(menu)
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator = indicator

    def show_board(self):
        was_visible = self.window.get_visible()
        self.window.present()
        if not was_visible:
            self.window.render()

    def show_search(self):
        self.show_board()
        self.window.show_search()

    def notify(self, title: str, body: str):
        # libnotify rather than Gio.Notification: it also works when not installed.
        try:
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify
            if not Notify.is_initted():
                Notify.init("Odjezdy")
            n = Notify.Notification.new(title, body, "odjezdy")
            n.set_urgency(Notify.Urgency.CRITICAL)    # stays up until seen
            n.show()
        except (ValueError, ImportError, GLib.Error) as e:
            print(f"odjezdy: notification failed ({e}): {title}", file=sys.stderr)

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if "--service" in args:           # started over D-Bus for the extension
            return 0
        if self.indicator is None and not self.attached:
            self._make_indicator()
        # A second launch reaches the running instance: show the board.
        if command_line.get_is_remote() or "--hidden" not in args or (
                self.indicator is None and not self.attached):
            self.show_board()
        return 0


def main() -> int:
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Odjezdy")
    return Application().run(sys.argv)
