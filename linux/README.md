# Odjezdy for Linux

A Linux port of [Odjezdy](../README.md): live departures for Prague (PID) and the rest of
Czechia, from a tray icon. Python 3 + GTK 3. Tested on Ubuntu 24.04 (GNOME, Wayland).

## Install

Build the package and install it:

    linux/packaging/build-deb.sh
    sudo apt install ./linux/dist/odjezdy_1.0.0_all.deb

Or run it from a checkout:

    sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
    python3 linux/run.py

## Drop-down board (GNOME Shell extension)

On GNOME 46 (Ubuntu 24.04) the extension makes it behave like the Mac app: click the tram in
the top bar and the board drops down under it. Right-click a departure for more actions.

After installing the `.deb`, log out and back in, then:

    gnome-extensions enable odjezdy@rp20048.github.io

From a checkout, run `linux/install-extension.sh` first and follow what it prints. To remove
that again, delete `~/.local/share/gnome-shell/extensions/odjezdy@rp20048.github.io` and
`~/.local/share/dbus-1/services/io.github.rp20048.Odjezdy.service`.

## Without the extension

The app shows its own tray icon: click it and choose **Show Departures** (or middle-click)
to open the board in a window.

## Board colour and text size

Like the Mac app, the departure board follows the desktop's style by default: off-white in
light, near-black in dark. Under ⚙ you can pick **Board colour** (Automatic, White, Off-White,
Dark) and **Text size** (Small to Extra Large). Sign-style amber text always uses the dark panel.

## Location

Automatic nearby stops use GeoClue. Turn on *Settings → Privacy & Security → Location
Services*. A fix less accurate than 500 m is ignored, because it would pick the wrong stops.
Desktops, trains and areas with little Wi-Fi often give only a rough position.

GeoClue's Wi-Fi lookup may need a working provider. To use the community service
[beaconDB](https://beacondb.net), add this under `[wifi]` in `/etc/geoclue/geoclue.conf`
and run `sudo systemctl restart geoclue`:

    url=https://api.beacondb.net/v1/geolocate

This sends identifiers of nearby Wi-Fi networks to beaconDB when an app asks for a location.

Without a good fix you can search for any stop, or pick one and choose
⚙ → *Use … as Home Location*.

## Differences from the Mac app

- Without the GNOME extension the board is a window, not a drop-down.
- With the extension, settings are in a panel under ⚙; there is no Start at Login (the app
  starts when you first open the board) and no Quit.
- Home location setting; rough fixes are rejected.
- Start at Login is off until you turn it on.
- The tray icon stays visible outside Czechia.
- Emoji replace the SF Symbols.

## Settings and privacy

Settings, recent stops and the optional Golemio key are kept in
`~/.config/odjezdy/config.json` (readable only by you). The app contacts `api.golemio.cz`
(with a key) and `motis.obehy.cz`, and sends them stop IDs, search text or coordinates.

## Development

    python3 -m unittest discover -s linux/tests
    ODJEZDY_DEBUG=1 ODJEZDY_LOCATION="50.0712,14.4033" python3 linux/run.py

The stop list and country border are shared with the Mac app (`Odjezdy/Resources`).
