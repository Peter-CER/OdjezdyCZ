"""D-Bus interface for the GNOME Shell extension (session bus, current user only)."""

from __future__ import annotations

import json

from gi.repository import Gio, GLib

from .view import build_view, perform

OBJECT_PATH = "/io/github/rp20048/Odjezdy/Board"
INTERFACE = "io.github.rp20048.Odjezdy.Board"
XML = f"""
<node><interface name="{INTERFACE}">
  <method name="Attach"/>
  <method name="Detach"/>
  <method name="SetOpen"><arg type="b" direction="in"/></method>
  <method name="Action"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
  <method name="GetView"><arg type="s" direction="out"/></method>
  <signal name="Changed"/>
</interface></node>
"""


class BoardService:
    def __init__(self, app):
        self.app = app
        self.connection = None
        self._id = 0

    def export(self, connection: Gio.DBusConnection) -> None:
        interface = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.connection = connection
        self._id = connection.register_object(OBJECT_PATH, interface, self._call, None, None)

    def unexport(self) -> None:
        if self._id:
            self.connection.unregister_object(self._id)
            self._id = 0

    def changed(self) -> None:
        if self._id:
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "Changed", None)

    def _call(self, _connection, _sender, _path, _interface, method, params, invocation):
        app = self.app
        try:
            result = None
            if method == "Attach":
                app.set_attached(True)
            elif method == "Detach":
                app.set_attached(False)
            elif method == "SetOpen":
                app.set_board_open(params.unpack()[0])
            elif method == "Action":
                action, arg = params.unpack()
                perform(app.state, app.alert, app.config, action, json.loads(arg))
                self.changed()
            elif method == "GetView":
                view = build_view(app.state, app.alert, app.config)
                result = GLib.Variant("(s)", (json.dumps(view, ensure_ascii=False),))
            invocation.return_value(result)
        except Exception as e:      # always answer, or the caller waits for a timeout
            invocation.return_dbus_error(f"{INTERFACE}.Error", str(e))
