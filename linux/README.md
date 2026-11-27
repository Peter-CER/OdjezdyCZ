# Odjezdy for Linux

A Linux port of [Odjezdy](../README.md): live departures for Prague (PID) and the rest of
Czechia, from the top bar. Python 3 + GTK 3. Tested on Ubuntu 24.04 (GNOME, Wayland).

**Requirements:** Ubuntu 24.04 or a similar system with Python 3.11 or newer. The drop-down
board needs GNOME Shell 46; elsewhere the app uses a tray icon and a window.

- [Install the package](#install-the-package) (recommended)
- [Run from a checkout](#run-from-a-checkout)
- [Update and uninstall](#update-and-uninstall)

## Install the package

1. Get `odjezdy_1.0.0_all.deb`. If the [latest release](../../../releases/latest) has it,
   download it. Otherwise build it (needs only `git` and `dpkg-deb`):

       git clone https://github.com/rp20048/OdjezdyCZ.git
       cd OdjezdyCZ
       linux/packaging/build-deb.sh        # → linux/dist/odjezdy_1.0.0_all.deb

2. Install it, which also installs what it depends on (use the path to your file):

       sudo apt install ./linux/dist/odjezdy_1.0.0_all.deb

3. **GNOME:** log out and back in, then turn on the drop-down:

       gnome-extensions enable odjezdy@rp20048.github.io

   A tram icon appears in the top bar. Click it for the board.

4. **Other desktops, or without the extension:** start **Odjezdy** from your applications (or
   run `odjezdy`), click its tray icon and choose **Show Departures** (or middle-click it).
   To start it at login, use ⚙ → *Start at Login* in the window.

There is no `apt install odjezdy` yet: the app is not in the Ubuntu archive or a PPA.

If you used `install-extension.sh` before, remove what it created (see below), or it takes
precedence over the installed package.

## Run from a checkout

    sudo apt install python3-gi gir1.2-gtk-3.0 gsettings-desktop-schemas gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
    python3 linux/run.py                 # tray icon and window

For the drop-down, run `linux/install-extension.sh`, log out and back in, then
`gnome-extensions enable odjezdy@rp20048.github.io`. It links to your checkout, so don't move
or delete that folder.

## Update and uninstall

**Package:** to update, build or download the newer `.deb` and install it the same way, then
log out and back in. To remove it: `sudo apt remove odjezdy`.

**Checkout:** to update, `git pull`, then log out and back in. To remove the drop-down:

    gnome-extensions disable odjezdy@rp20048.github.io
    rm ~/.local/share/gnome-shell/extensions/odjezdy@rp20048.github.io
    rm ~/.local/share/dbus-1/services/io.github.rp20048.Odjezdy.service

Your settings stay in `~/.config/odjezdy/`; delete that folder to remove them too.

## Using it

- **Left-click** a departure for a leave-now alert; click it again to cancel.
- **Right-click** a departure for the alert and to add its line to **Your Lines**.
- **More ▾** shows later departures; **🔍** searches any stop.
- **⚙** has the board settings, alert lead time, home location and the optional
  [Golemio key](../README.md#first-start-the-golemio-api-key) for Prague.

## Troubleshooting

- **No tram icon after enabling:** log out and back in, then check
  `gnome-extensions info odjezdy@rp20048.github.io`.
- **"The Odjezdy app is not responding":** run `odjezdy` (or `python3 linux/run.py`) in a
  terminal to see the error.
- **Changes don't show after updating:** `pkill -f "odjezdy|linux/run.py|run.py --service"`,
  then log out and back in.
- **Wrong or no nearby stops:** see [Location](#location).

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
- Start at Login (window version) is off until you turn it on.
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
