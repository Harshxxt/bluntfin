"""Local web app. Standard library only. Binds to 127.0.0.1 by default.

Nothing is stored: each request is analyzed in memory and discarded.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from .analysis import priorities, run
from .countries import apply_country, public_countries
from .model import ProfileError, profile_from_dict

MAX_BODY = 64 * 1024
MAX_RUNS = 20_000


def analyze_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ProfileError("Request body must be a JSON object.")
    opts = payload.pop("_options", {}) or {}
    data = apply_country(payload)
    profile = profile_from_dict(data)
    sections = opts.get("sections") or None
    runs = int(opts.get("runs", 5000))
    if not 100 <= runs <= MAX_RUNS:
        raise ProfileError(f"runs must be between 100 and {MAX_RUNS}.")
    seed = opts.get("seed")
    extra = opts.get("extra")
    findings = run(profile, sections, None if extra in (None, "") else float(extra),
                   monte_carlo=bool(opts.get("monte_carlo", True)), runs=runs,
                   seed=None if seed in (None, "") else int(seed))
    return {
        "currency": profile.currency,
        "findings": [{"section": f.section, "severity": f.severity.name, "headline": f.headline,
                      "detail": f.detail, "metrics": f.metrics} for f in findings],
        "priorities": [f.headline for f in priorities(findings)],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "bluntfin"

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            html = resources.files("bluntfin").joinpath("web/index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path == "/api/countries":
            self._json(200, public_countries())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/api/analyze":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._json(413 if length > MAX_BODY else 400, {"error": "Body missing or too large."})
        try:
            payload = json.loads(self.rfile.read(length))
            self._json(200, analyze_payload(payload))
        except json.JSONDecodeError:
            self._json(400, {"error": "Body is not valid JSON."})
        except (ProfileError, ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:  # never leak a traceback to the browser
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal error."})
            raise

    def log_message(self, fmt, *args):  # keep financial data out of logs: log path only
        print(f"{self.address_string()} {self.command} {self.path.split('?')[0]}")


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"bluntfin running at http://{host}:{port}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening beyond this machine. Anyone on your network can submit data to it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
