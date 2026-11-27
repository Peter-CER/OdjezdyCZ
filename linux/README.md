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

Click the tram icon in the top bar and choose **Show Departures** (or middle-click it).

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

- The board is a window opened from the tray menu, not a popover.
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
