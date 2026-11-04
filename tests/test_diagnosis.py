"""Unit tests for the GNSS Quality Diagnostics & Time-Window Slicing Engine."""
import unittest
import numpy as np

from qc.diagnosis import (
    parse_timestamp,
    slice_analysis_result,
    detect_sky_obstruction,
    evaluate_quality_issues,
    diagnose_station_window,
)


class TestDiagnosis(unittest.TestCase):
    def setUp(self):
        # Create a realistic synthetic analysis result spanning 100 epochs (interval 30s)
        self.t0 = 1793174400.0  # reference GPST
        self.interval = 30.0
        self.epochs = 100

        series = []
        for i in range(self.epochs):
            t = self.t0 + i * self.interval
            series.append({
                "time": t,
                "satellites": 25,
                "used": 20,
                "pdop": 1.5 + 0.1 * np.sin(i / 10.0),
                "snr": 42.0 + np.cos(i / 5.0),
                "by_system": {
                    "G": {"satellites": 8, "used": 7, "snr": 42.5, "pdop": 2.1},
                    "R": {"satellites": 6, "used": 5, "snr": 39.5, "pdop": 2.8},
                    "E": {"satellites": 5, "used": 4, "snr": 41.0, "pdop": 3.2},
                    "C": {"satellites": 10, "used": 8, "snr": 43.0, "pdop": 1.9},
                }
            })

        # Synthetic satellites with track points: [epoch_idx, az, el, snr, geom_status]
        satellites = []
        for sat_idx, sat_id in enumerate(["G01", "G02", "R05", "E12", "C06", "C14"]):
            track = []
            for i in range(self.epochs):
                az = (sat_idx * 60 + i * 4) % 360
                el = 10.0 + 50.0 * abs(np.sin(i / 10.0 + sat_idx))
                track.append([i, az, el, 42.0, 0])
            satellites.append({
                "id": sat_id,
                "system": sat_id[0],
                "track": track
            })

        events = [
            {"epoch": 10, "sat": "G01", "time": "2026-10-28T08:05:00.000", "detectors": ["GF"]},
            {"epoch": 50, "sat": "C06", "time": "2026-10-28T08:25:00.000", "detectors": ["MW", "LLI"]},
        ]

        self.mock_result = {
            "files": {"obs": {"name": "TEST01_20261028.26o"}},
            "interval": self.interval,
            "start": "2026-10-28T08:00:00.000",
            "end": "2026-10-28T08:49:30.000",
            "series": series,
            "satellites": satellites,
            "events": events
        }

    def test_parse_timestamp(self):
        ts = parse_timestamp(1793174400.0)
        self.assertEqual(ts, 1793174400.0)
        ts_iso = parse_timestamp("2026-10-28T08:00:00.000")
        self.assertIsInstance(ts_iso, float)

    def test_slice_window(self):
        t_start = self.t0 + 10 * self.interval
        t_end = self.t0 + 40 * self.interval

        sliced = slice_analysis_result(self.mock_result, t_start, t_end)
        self.assertFalse(sliced["empty"])
        self.assertEqual(sliced["summary"]["epochs"], 31)
        self.assertAlmostEqual(sliced["summary"]["completeness"], 1.0)
        self.assertEqual(len(sliced["events"]), 1)
        self.assertEqual(sliced["events"][0]["sat"], "G01")
        self.assertIn("G", sliced["by_system"])
        self.assertIn("C", sliced["by_system"])

    def test_detect_sky_obstruction_clean(self):
        # Open sky with satellite tracks going down to ~10-15°
        obstr = detect_sky_obstruction(self.mock_result["satellites"])
        self.assertFalse(obstr["has_obstruction"])
        self.assertEqual(len(obstr["sectors"]), 0)

    def test_detect_sky_obstruction_with_obstacle(self):
        # Inject physical obstruction in azimuth 210°-240° cutting off all tracks below 30°
        blocked_sats = []
        for sat in self.mock_result["satellites"]:
            new_track = []
            for pt in sat["track"]:
                i, az, el, snr, status = pt
                if 210 <= az < 240:
                    new_track.append([i, az, max(32.0, el), snr, status])
                else:
                    new_track.append(pt)
            blocked_sats.append({**sat, "track": new_track})

        obstr = detect_sky_obstruction(blocked_sats)
        self.assertTrue(obstr["has_obstruction"])
        self.assertGreaterEqual(len(obstr["sectors"]), 1)
        sector = obstr["sectors"][0]
        self.assertGreaterEqual(sector["cutoff_elevation"], 30.0)
        self.assertEqual(sector["az_start"], 210)
        self.assertEqual(sector["az_end"], 240)

    def test_evaluate_quality_issues_normal(self):
        sliced = slice_analysis_result(self.mock_result)
        diag = evaluate_quality_issues(sliced)
        self.assertEqual(diag["status"], "PASS")
        self.assertGreaterEqual(diag["score"], 90)
        self.assertIn("测站正常", diag["summary_text"])

    def test_evaluate_quality_issues_with_anomalies(self):
        # Make a copy with degraded SNR and high cycle slips
        degraded = dict(self.mock_result)
        degraded["series"] = [
            {**r, "snr": 34.0, "pdop": 5.8, "used": 4} for r in degraded["series"]
        ]
        degraded["events"] = [
            {"epoch": i, "sat": "G01", "time": "2026-10-28T08:00:00.000", "detectors": ["GF"]}
            for i in range(50)
        ]

        sliced = slice_analysis_result(degraded)
        diag = evaluate_quality_issues(sliced)
        self.assertEqual(diag["status"], "CRITICAL")
        self.assertLess(diag["score"], 60)
        codes = [iss["code"] for iss in diag["issues"]]
        self.assertTrue(any("SNR" in c for c in codes))
        self.assertTrue(any("PDOP" in c for c in codes))
        self.assertTrue(any("CYCLE" in c for c in codes))

    def test_diagnose_station_window(self):
        out = diagnose_station_window(self.mock_result)
        self.assertEqual(out["station"], "TEST01")
        self.assertEqual(out["status"], "PASS")
        self.assertIn("window", out)
        self.assertIn("by_system", out)
        self.assertIn("report_text", out)


if __name__ == "__main__":
    unittest.main()
