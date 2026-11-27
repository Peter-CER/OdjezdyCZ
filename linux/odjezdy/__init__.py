"""Odjezdy for Linux: live Czech public transport departures in the system tray.

A port of the macOS app in ../Odjezdy (Swift). The logic mirrors it file by file:
models.py ↔ Models.swift/Region.swift/CountryBorder.swift, stops.py ↔ StopIndex.swift,
providers.py ↔ GolemioClient.swift/MotisClient.swift, state.py ↔ AppState.swift,
alerts.py ↔ LeaveAlert.swift, location.py ↔ LocationService.swift, ui.py ↔ PopoverView.swift +
DeparturesView.swift, app.py ↔ OdjezdyApp.swift.
"""

__version__ = "1.0.0"
APP_ID = "io.github.rp20048.Odjezdy"
REPO_URL = "https://github.com/rp20048/OdjezdyCZ"
