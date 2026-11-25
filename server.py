#!/usr/bin/env python3
"""Zero-dependency HTTP server for the MixLens BSS workbench.

Run from the repository root:

    python3 server.py

Then open http://127.0.0.1:8000.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bss_lab.analysis import AnalysisError, analyze, run_audit  # noqa: E402

WEB_ROOT = ROOT / "web"
MAX_BODY = 2_000_000


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # Keep test/server output concise; errors still surface via status.
        if os.environ.get("MIXLENS_DEBUG"):
            super().log_message(fmt, *args)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/api/health":
            self._send_json({"ok": True, "service": "mixlens-bss"})
            return
        if self.path.split("?", 1)[0] == "/api/audit":
            try:
                self._send_json(run_audit())
            except Exception as exc:  # pragma: no cover - server guard
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        if self.path in ("", "/"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/api/analyze", "/api/audit"}:
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._send_json({"ok": False, "error": "请求体为空或过大"}, 413)
                return
            raw = self.rfile.read(length)
            config = json.loads(raw.decode("utf-8"))
            if not isinstance(config, dict):
                raise AnalysisError("配置必须是 JSON 对象")
            if path == "/api/audit":
                payload = run_audit()
            else:
                payload = analyze(config)
            self._send_json(payload)
        except AnalysisError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json({"ok": False, "error": f"JSON/数值错误：{exc}"}, 400)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"服务器错误：{exc}"}, 500)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"MixLens BSS workbench: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
