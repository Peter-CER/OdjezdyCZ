"""Headless tests of the ported logic: python3 -m unittest discover -s linux/tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from odjezdy import models, providers, stops  # noqa: E402
from odjezdy.alerts import Pending, message  # noqa: E402
from odjezdy.config import Config  # noqa: E402
from odjezdy.state import build_sections  # noqa: E402

NOW = models.parse_time("2026-11-27T12:00:00Z")


def departure(line, minutes, mode=models.TRAM, **kw):
    t = NOW + minutes * 60
    return models.Departure(f"{line}-{minutes}", "x", line, mode, "Dest", t, t, "planned",
                            False, **kw)


def stop_group(distance=100):
    return models.StopGroup("Anděl", "Praha", distance, 50.071, 14.403, ["x"], models.TRAM, 100, [])


class Data(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = stops.StopIndex.load(os.path.join(models.data_dir(), "stops_prague.json"))

    def test_stop_list_loads(self):
        self.assertGreater(len(self.index.stops), 7000)

    def test_search_ignores_accents(self):
        self.assertEqual(self.index.search("hlavni nadr")[0].name, "Hlavní nádraží")

    def test_best_stops_near_andel(self):
        found = self.index.best_stops(50.0712, 14.4033)
        self.assertEqual(found[0].name, "Anděl")
        self.assertLessEqual(len(found), 3)

    def test_border(self):
        border = models.CountryBorder(os.path.join(models.data_dir(), "cz_border.json"))
        self.assertTrue(border.contains(49.195, 16.608))    # Brno
        self.assertFalse(border.contains(48.208, 16.373))   # Vienna


class Parsing(unittest.TestCase):
    def test_golemio(self):
        board = providers.parse_golemio({
            "infotexts": [{"text": "Zavřeno", "text_en": "Closed"}],
            "departures": [{
                "route": {"short_name": "A", "type": 1},
                "trip": {"id": "t", "headsign": "Depo"},
                "stop": {"id": "U1Z1P"},
                "delay": {"is_available": True},
                "departure_timestamp": {"scheduled": "2026-11-27T13:00:00+01:00",
                                        "predicted": "2026-11-27T13:03:00.000+01:00"}}]}, NOW)
        d = board.departures[0]
        self.assertEqual((d.line, d.mode, d.delay_minutes, d.is_live), ("A", models.METRO, 3, True))
        self.assertEqual(board.notices, ["Closed"])

    def test_motis(self):
        deps, alerts = providers.parse_stoptimes({"stopTimes": [{
            "routeShortName": "S3 (Os 4658)", "mode": "SUBURBAN", "headsign": "Kolín",
            "place": {"scheduledDeparture": "2026-11-27T12:05:00Z",
                      "alerts": [{"headerText": "Works", "descriptionText": "Bus"}]}}]}, "id", NOW)
        self.assertEqual((deps[0].line, deps[0].mode, deps[0].precise), ("S3", models.TRAIN, False))
        self.assertEqual(alerts, ["Works: Bus"])


class Logic(unittest.TestCase):
    def test_uncatchable_departures_are_skipped(self):
        deps = [departure("1", m) for m in range(0, 20)]
        section = build_sections(stop_group(800), deps, set(), set(), NOW)[0]
        # 800 m ≈ 750 s walk; 0.75 × that ≈ 9.4 min → one missed departure kept, then 10 min on.
        self.assertEqual(section.departures[0].id, "1-9")
        self.assertEqual(len(section.departures), 8)
        self.assertTrue(section.has_more)

    def test_favourites_get_their_own_section_first(self):
        deps = [departure("1", 5), departure("2", 6), departure("A", 7, models.METRO)]
        sections = build_sections(stop_group(), deps, {"2"}, set(), NOW)
        self.assertEqual([s.id for s in sections], [-1, models.METRO, models.TRAM])

    def test_alert_message(self):
        p = Pending("d", stop_group(160), "26", "Nádraží", NOW + 20 * 60, 5)
        title, body = message(p, p.fire_at)
        self.assertEqual(title, "Leave within 5 min for 26 → Nádraží")
        self.assertIn("3 min walk", body)

    def test_badge_text_is_dark_on_yellow_and_white_is_replaced(self):
        self.assertEqual(models.badge_colors("FFD400", models.METRO)[1], "rgba(0,0,0,0.85)")
        self.assertEqual(models.badge_colors("FFFFFF", models.BUS)[0], "#52575F")

    def test_config_is_private_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            Config(path).set("golemioKey", "secret")
            self.assertEqual(Config(path).golemio_key, "secret")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
