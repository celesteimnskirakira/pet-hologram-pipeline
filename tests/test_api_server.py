"""Security and contract tests for the public generation bridge."""

from __future__ import annotations

import time
import unittest
import sys
from email.message import Message
from pathlib import Path
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import api_server


class ApiServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.originals = {
            "BACKEND_SECRET": api_server.BACKEND_SECRET,
            "CALLBACK_SECRET": api_server.CALLBACK_SECRET,
            "ARTIFACT_SECRET": api_server.ARTIFACT_SECRET,
            "PROJECTION_SECRET": api_server.PROJECTION_SECRET,
            "ALLOWED_IMAGE_HOSTS": api_server.ALLOWED_IMAGE_HOSTS,
            "ALLOWED_CALLBACK_HOSTS": api_server.ALLOWED_CALLBACK_HOSTS,
            "ALLOW_LOCAL_HTTP": api_server.ALLOW_LOCAL_HTTP,
            "JOB_SLOTS": api_server.JOB_SLOTS,
        }
        api_server.BACKEND_SECRET = "backend-test-secret"
        api_server.CALLBACK_SECRET = "callback-test-secret"
        api_server.ARTIFACT_SECRET = "artifact-test-secret"
        api_server.PROJECTION_SECRET = "device-test-secret"
        api_server.ALLOWED_IMAGE_HOSTS = ("storage.example.test",)
        api_server.ALLOWED_CALLBACK_HOSTS = ("app.example.test",)
        api_server.ALLOW_LOCAL_HTTP = False
        with api_server.LOCK:
            api_server.JOBS.clear()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(api_server, name, value)
        with api_server.LOCK:
            api_server.JOBS.clear()

    def test_jobs_require_backend_secret(self):
        handler = api_server.Handler.__new__(api_server.Handler)
        handler.headers = Message()
        self.assertFalse(handler._backend_authorized())
        handler.headers["X-Generation-Backend-Secret"] = api_server.BACKEND_SECRET
        self.assertTrue(handler._backend_authorized())

    def test_allowed_https_urls_are_accepted(self):
        image = api_server._validate_remote_url(
            "https://storage.example.test/pets/cat.jpg",
            allowlist=api_server.ALLOWED_IMAGE_HOSTS,
            purpose="image",
        )
        self.assertEqual(image, "https://storage.example.test/pets/cat.jpg")

    def test_url_credentials_and_unlisted_hosts_are_rejected(self):
        cases = (
            "http://storage.example.test/cat.jpg",
            "https://127.0.0.1/cat.jpg",
            "https://user:pass@storage.example.test/cat.jpg",
            "https://evil.example.test/cat.jpg",
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                api_server._validate_remote_url(
                    value,
                    allowlist=api_server.ALLOWED_IMAGE_HOSTS,
                    purpose="image",
                )

    def test_signed_artifact_url_expires_and_rejects_bad_signature(self):
        expires = int(time.time()) + 60
        signature = api_server._artifact_signature("task_abcdef", "clip.mp4", expires)
        valid = {"expires": [str(expires)], "signature": [signature]}
        self.assertTrue(api_server._artifact_authorized("task_abcdef", "clip.mp4", valid))
        self.assertFalse(
            api_server._artifact_authorized(
                "task_abcdef", "clip.mp4", {**valid, "signature": ["0" * 64]}
            )
        )
        expired = int(time.time()) - 1
        self.assertFalse(
            api_server._artifact_authorized(
                "task_abcdef",
                "clip.mp4",
                {
                    "expires": [str(expired)],
                    "signature": [api_server._artifact_signature("task_abcdef", "clip.mp4", expired)],
                },
            )
        )

    def test_artifact_path_traversal_is_rejected(self):
        expires = int(time.time()) + 60
        name = "../secret"
        signature = api_server._artifact_signature("task_abcdef", name, expires)
        self.assertFalse(
            api_server._artifact_request_allowed(
                "task_abcdef",
                name,
                {"expires": [str(expires)], "signature": [signature]},
            )
        )

    def test_capacity_limit_returns_429_without_starting_a_job(self):
        class FullCapacity:
            def acquire(self, blocking=False):
                return False

        api_server.JOB_SLOTS = FullCapacity()
        self.assertFalse(api_server._reserve_job_slot())

    def test_device_bearer_authentication(self):
        self.assertTrue(api_server._device_authorized("Bearer device-test-secret"))
        self.assertTrue(api_server._device_authorized("bearer device-test-secret"))
        self.assertFalse(api_server._device_authorized("device-test-secret"))
        self.assertFalse(api_server._device_authorized("Bearer wrong-secret"))

    def test_device_queue_and_played_acknowledgement(self):
        event = api_server.threading.Event()
        job = {
            "task_id": "task_abcdef",
            "display_code": "123456",
            "status": "processing",
            "stage": "delivering",
            "deliveryStatus": "waiting_for_device",
            "delivery_url": "https://api.example.test/video.mp4?signature=test",
            "delivery_name": "pet-loop.mp4",
            "delivery_sha256": "a" * 64,
            "_delivery_event": event,
        }
        with api_server.LOCK:
            api_server.JOBS[job["task_id"]] = job

        item = api_server._next_device_item()
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], "task_abcdef")
        self.assertEqual(item["name"], "pet-loop.mp4")
        self.assertTrue(item["ack_url"].endswith("/api/device/ack"))
        self.assertTrue(api_server._acknowledge_device("task_abcdef", "played"))
        self.assertTrue(event.is_set())
        self.assertEqual(job["deliveryStatus"], "ready")
        self.assertIsNone(api_server._next_device_item())

    def test_device_ack_rejects_invalid_or_non_waiting_job(self):
        self.assertFalse(api_server._acknowledge_device("bad", "played"))
        self.assertFalse(api_server._acknowledge_device("task_missing", "played"))
        with api_server.LOCK:
            api_server.JOBS["task_abcdef"] = {
                "task_id": "task_abcdef",
                "status": "processing",
                "stage": "generating_video",
            }
        self.assertFalse(api_server._acknowledge_device("task_abcdef", "played"))

    def test_callback_failure_is_recorded_without_crashing(self):
        job = {
            "task_id": "task_abcdef",
            "callback_url": "https://app.example.test/api/generation/task_abcdef",
            "status": "queued",
            "stage": "queued",
        }
        with mock.patch.object(api_server.urllib.request, "urlopen", side_effect=TimeoutError("timeout")):
            api_server._callback(job, status="processing", stage="validating")
        self.assertEqual(job["status"], "processing")
        self.assertIn("timeout", job["last_callback_error"])

    def test_callback_persists_frontend_visible_stage_fields(self):
        job = {
            "task_id": "task_abcdef",
            "callback_url": "https://app.example.test/api/generation/task_abcdef",
            "status": "queued",
            "stage": "queued",
        }
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch.object(api_server.urllib.request, "urlopen", return_value=response) as opened:
            api_server._callback(
                job,
                status="processing",
                stage="generating_video",
                progress=60,
                message="正在生成动作视频",
                selectedAction="走路",
            )
        self.assertEqual(job["progress"], 60)
        self.assertEqual(job["message"], "正在生成动作视频")
        self.assertEqual(job["selectedAction"], "走路")
        self.assertIn("updatedAt", job)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "petloop-api/1.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
