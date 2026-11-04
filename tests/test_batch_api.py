"""Integration tests for the batch QC and time-window endpoints."""
import unittest
import numpy as np
from fastapi.testclient import TestClient

from server import app, jobs


class TestBatchAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        jobs.clear()

        # Build synthetic jobs
        t0 = 1793174400.0  # 2026-11-04 00:00:00 GPST
        interval = 30.0
        epochs = 120

        series1 = []
        series2 = []
        for i in range(epochs):
            t = t0 + i * interval
            # Station 1: Good station
            series1.append({
                "time": t,
                "satellites": 28,
                "used": 24,
                "pdop": 1.4,
                "snr": 43.0,
                "by_system": {
                    "G": {"satellites": 9, "used": 8, "snr": 43.5, "pdop": 1.8},
                    "R": {"satellites": 6, "used": 5, "snr": 40.5, "pdop": 2.5},
                    "E": {"satellites": 5, "used": 4, "snr": 42.0, "pdop": 2.9},
                    "C": {"satellites": 12, "used": 10, "snr": 44.0, "pdop": 1.6},
                }
            })
            # Station 2: Degraded station (low BDS SNR, higher PDOP)
            series2.append({
                "time": t,
                "satellites": 14,
                "used": 10,
                "pdop": 3.8,
                "snr": 36.2,
                "by_system": {
                    "G": {"satellites": 5, "used": 4, "snr": 41.0, "pdop": 3.2},
                    "R": {"satellites": 3, "used": 2, "snr": 37.0, "pdop": 4.5},
                    "E": {"satellites": 2, "used": 1, "snr": 38.0, "pdop": 5.2},
                    "C": {"satellites": 4, "used": 3, "snr": 33.5, "pdop": 4.0},
                }
            })

        # Satellites for Station 1
        sats1 = []
        for s_idx, sat_id in enumerate(["G01", "G02", "R05", "E12", "C06", "C14"]):
            track = []
            for i in range(epochs):
                az = (s_idx * 60 + i * 4) % 360
                el = 10.0 + 50.0 * abs(np.sin(i / 10.0 + s_idx))
                track.append([i, float(az), float(el), 43.0, 0])
            sats1.append({"id": sat_id, "system": sat_id[0], "track": track})

        # Satellites for Station 2: obstacle in 120-150 deg (el always >= 28)
        sats2 = []
        for s_idx, sat_id in enumerate(["G01", "G03", "R07", "C01", "C02", "C03"]):
            track = []
            for i in range(epochs):
                az = (s_idx * 60 + i * 4) % 360
                el = 10.0 + 50.0 * abs(np.sin(i / 10.0 + s_idx))
                if 120.0 <= az <= 150.0 and el < 30.0:
                    el = 30.0
                track.append([i, float(az), float(el), 36.0, 0])
            sats2.append({"id": sat_id, "system": sat_id[0], "track": track})

        res1 = {
            "summary": {
                "station": "STA1",
                "start_time": "2026-10-28T08:00:00Z",
                "end_time": "2026-10-28T09:00:00Z",
                "epochs": epochs,
                "interval": interval,
                "quality_score": 96.0,
                "completeness": 100.0,
                "slips_per_1000_epochs": 1.2,
            },
            "series": series1,
            "satellites": sats1,
            "events": [],
        }

        res2 = {
            "summary": {
                "station": "STA2",
                "start_time": "2026-10-28T08:00:00Z",
                "end_time": "2026-10-28T09:00:00Z",
                "epochs": epochs,
                "interval": interval,
                "quality_score": 68.0,
                "completeness": 92.0,
                "slips_per_1000_epochs": 14.5,
            },
            "series": series2,
            "satellites": sats2,
            "events": [{"epoch": 10, "sat": "C01", "type": "GF"}],
        }

        job1 = {
            "id": "job_sta1",
            "status": "done",
            "progress": 100,
            "message": "分析完成",
            "created": 1000.0,
            "label": "STA1.26o",
            "error": None,
            "result": res1,
            "owned": None,
        }
        job2 = {
            "id": "job_sta2",
            "status": "done",
            "progress": 100,
            "message": "分析完成",
            "created": 1001.0,
            "label": "STA2.26o",
            "error": None,
            "result": res2,
            "owned": None,
        }

        jobs["job_sta1"] = job1
        jobs["job_sta2"] = job2

    def tearDown(self):
        jobs.clear()

    def test_batch_timespan(self):
        resp = self.client.get("/api/batch/timespan")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["has_jobs"])
        self.assertEqual(len(data["station_spans"]), 2)
        self.assertIsNotNone(data["common_start"])
        self.assertIsNotNone(data["common_end"])

    def test_batch_diagnostics_all(self):
        resp = self.client.post("/api/batch/diagnostics", json={})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("stations", data)
        self.assertEqual(len(data["stations"]), 2)

        sta_map = {s["station"]: s for s in data["stations"]}
        self.assertIn("STA1", sta_map)
        self.assertIn("STA2", sta_map)
        self.assertEqual(sta_map["STA1"]["status"], "PASS")
        self.assertGreaterEqual(sta_map["STA1"]["score"], 90.0)

        # STA2 has issues: low SNR and high PDOP
        self.assertIn(sta_map["STA2"]["status"], ["WARN", "CRITICAL"])
        self.assertLess(sta_map["STA2"]["score"], 90.0)

    def test_batch_diagnostics_window_slice(self):
        # Slice middle 30 minutes
        resp = self.client.post("/api/batch/diagnostics", json={
            "start_time": "2026-10-28T08:15:00Z",
            "end_time": "2026-10-28T08:45:00Z"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("window", data)
        self.assertEqual(len(data["stations"]), 2)
        for s in data["stations"]:
            self.assertGreater(s["summary"]["epochs"], 0)
            self.assertLess(s["summary"]["epochs"], 120)

    def test_job_window_drilldown(self):
        resp = self.client.get("/api/jobs/job_sta1/window?start_time=2026-10-28T08:10:00Z&end_time=2026-10-28T08:50:00Z")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("series", data)
        self.assertIn("satellites", data)
        self.assertIn("diagnostics", data)
        self.assertEqual(data["station"], "STA1")
        self.assertEqual(data["diagnostics"]["status"], "PASS")
        # Check satellite tracks are returned
        self.assertGreater(len(data["satellites"]), 0)
        self.assertIn("track", data["satellites"][0])


if __name__ == "__main__":
    unittest.main()
