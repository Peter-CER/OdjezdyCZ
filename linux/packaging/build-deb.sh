#!/bin/sh
# Build linux/dist/odjezdy_<version>_all.deb. Needs only dpkg-deb.
set -eu
linux=$(cd "$(dirname "$0")/.." && pwd)
resources="$linux/../Odjezdy/Resources"
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$linux/odjezdy/__init__.py")
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

install -d "$root/DEBIAN" "$root/usr/bin" "$root/usr/lib/python3/dist-packages/odjezdy" \
    "$root/usr/share/odjezdy" "$root/usr/share/applications" "$root/usr/share/doc/odjezdy" \
    "$root/usr/share/icons/hicolor/scalable/apps" "$root/usr/share/icons/hicolor/256x256/apps"
install -m644 "$linux"/odjezdy/*.py "$root/usr/lib/python3/dist-packages/odjezdy/"
install -m644 "$resources/stops_prague.json" "$resources/cz_border.json" "$root/usr/share/odjezdy/"
install -m644 "$linux/packaging/io.github.rp20048.Odjezdy.desktop" "$root/usr/share/applications/"
install -m644 "$linux/data/odjezdy-symbolic.svg" "$root/usr/share/icons/hicolor/scalable/apps/"
install -m644 "$linux/data/odjezdy.png" "$root/usr/share/icons/hicolor/256x256/apps/"
install -m644 "$linux/../LICENSE" "$root/usr/share/doc/odjezdy/copyright"

uuid=odjezdy@rp20048.github.io
install -d "$root/usr/share/gnome-shell/extensions/$uuid" "$root/usr/share/dbus-1/services"
install -m644 "$linux/extension/$uuid"/* "$root/usr/share/gnome-shell/extensions/$uuid/"
install -m644 "$linux/packaging/io.github.rp20048.Odjezdy.service" "$root/usr/share/dbus-1/services/"

cat > "$root/usr/bin/odjezdy" <<'LAUNCHER'
#!/usr/bin/python3
from odjezdy.app import main

raise SystemExit(main())
LAUNCHER
chmod 755 "$root/usr/bin/odjezdy"

cat > "$root/DEBIAN/control" <<CONTROL
Package: odjezdy
Version: $version
Architecture: all
Maintainer: Odjezdy contributors <noreply@github.com>
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-3.0, gsettings-desktop-schemas, gir1.2-ayatanaappindicator3-0.1, gir1.2-notify-0.7
Recommends: gir1.2-geoclue-2.0, geoclue-2.0, gnome-shell-extension-appindicator, fonts-noto-color-emoji
Section: utils
Priority: optional
Homepage: https://github.com/rp20048/OdjezdyCZ
Description: Czech public transport departures in the system tray
 Live departures for Prague (PID) and the rest of Czechia, with nearby stops,
 stop search, favourite lines and leave-now alerts.
CONTROL

chmod -R u+rwX,go+rX,go-w "$root"
mkdir -p "$linux/dist"
dpkg-deb --root-owner-group --build "$root" "$linux/dist/odjezdy_${version}_all.deb"
