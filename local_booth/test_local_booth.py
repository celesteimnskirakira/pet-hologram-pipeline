from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import device_bridge
import server


class LocalBoothTests(unittest.TestCase):
    def test_old_uploader_is_not_imported(self) -> None:
        imported = " ".join(sys.modules)
        self.assertNotIn("HoloUploader", imported)
        self.assertNotIn("mac_uploader", imported)

    def test_device_output_stays_in_local_job_directory(self) -> None:
        path = server.RUNS_DIR / "abc123" / "device" / "current.avi"
        self.assertTrue(path.is_relative_to(server.LOCAL_ROOT))

    def test_port_error_is_clear(self) -> None:
        with self.assertRaisesRegex(device_bridge.DeviceBridgeError, "USB 串口不存在"):
            device_bridge.find_port("/tmp/definitely-not-a-real-serial-port")

    def test_job_persistence_is_local(self) -> None:
        old = server.RUNS_DIR
        try:
            with tempfile.TemporaryDirectory() as raw:
                server.RUNS_DIR = Path(raw)
                job = {"id": "job1", "status": "queued", "events": ["secret"]}
                server._persist(job)
                saved = (Path(raw) / "job1" / "job.json").read_text(encoding="utf-8")
                self.assertIn('"status": "queued"', saved)
                self.assertNotIn("events", saved)
        finally:
            server.RUNS_DIR = old

    def test_generated_loop_is_sent_by_independent_bridge(self) -> None:
        old_runs = server.RUNS_DIR
        old_jobs = server.JOBS
        old_settings = dict(server.SETTINGS)
        try:
            with tempfile.TemporaryDirectory() as raw:
                server.RUNS_DIR = Path(raw)
                server.JOBS = {
                    "job1": {
                        "id": "job1",
                        "pet_name": "奶糖",
                        "pose": "curled_side",
                        "pose_name": "侧卧蜷睡",
                        "image": "/tmp/photo.jpg",
                        "status": "queued",
                        "stage": "等待生成",
                        "progress": 0.0,
                        "events": [],
                    }
                }
                server.SETTINGS.update({"no_device": False, "port": None})
                generated = Path(raw) / "generated.mp4"
                artifacts = SimpleNamespace(clips=[{"ok": True, "loop": str(generated)}])
                expected_avi = Path(raw) / "job1" / "device" / "current.avi"
                with (
                    mock.patch.object(server.pipeline, "run", return_value=artifacts),
                    mock.patch.object(
                        server,
                        "convert_and_upload",
                        return_value=(expected_avi, "/dev/cu.usbmodem3101"),
                    ) as bridge,
                ):
                    server.process_job("job1")
                bridge.assert_called_once()
                self.assertEqual(server.JOBS["job1"]["status"], "done")
                self.assertEqual(server.JOBS["job1"]["device"], "/dev/cu.usbmodem3101")
                self.assertEqual(server.JOBS["job1"]["avi"], str(expected_avi))
        finally:
            server.RUNS_DIR = old_runs
            server.JOBS = old_jobs
            server.SETTINGS.clear()
            server.SETTINGS.update(old_settings)


if __name__ == "__main__":
    unittest.main()
