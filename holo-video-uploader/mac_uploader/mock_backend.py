#!/usr/bin/env python3

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Holo backend API for integration testing")
    parser.add_argument("mp4", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    video = args.mp4.resolve().read_bytes()
    video_name = args.mp4.name

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/device/next":
                body = json.dumps({
                    "id": "local-test-001",
                    "name": video_name,
                    "download_url": f"http://127.0.0.1:{args.port}/video.mp4",
                    "ack_url": f"http://127.0.0.1:{args.port}/api/device/ack",
                }).encode()
                self.send_bytes(200, "application/json", body)
            elif self.path == "/video.mp4":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(video)))
                self.end_headers()
                self.wfile.write(video)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/api/device/ack":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length).decode("utf-8", errors="replace")
            print(f"ACK {payload}", flush=True)
            self.send_bytes(200, "application/json", b'{"ok":true}')

        def log_message(self, message: str, *values: object) -> None:
            print(f"{self.command} {self.path} - {message % values}", flush=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"MOCK BACKEND http://127.0.0.1:{args.port}/api/device/next", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
