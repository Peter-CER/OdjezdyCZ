#!/bin/sh
# Install the GNOME Shell extension for this user, running the app from this checkout.
# (Not needed with the .deb, which installs both system-wide.)
set -eu
linux=$(cd "$(dirname "$0")" && pwd)
uuid=odjezdy@rp20048.github.io
data=${XDG_DATA_HOME:-$HOME/.local/share}
extensions="$data/gnome-shell/extensions"

mkdir -p "$extensions" "$data/dbus-1/services"
ln -sfn "$linux/extension/$uuid" "$extensions/$uuid"
cat > "$data/dbus-1/services/io.github.rp20048.Odjezdy.service" <<SERVICE
[D-BUS Service]
Name=io.github.rp20048.Odjezdy
Exec=/usr/bin/python3 "$linux/run.py" --service
SERVICE
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.ReloadConfig >/dev/null

echo "Installed. Now log out and back in, then run:"
echo "    gnome-extensions enable $uuid"
