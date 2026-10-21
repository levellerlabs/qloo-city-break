import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from http.server import BaseHTTPRequestHandler  # noqa: E402

from _lib.planner import PlanError, plan_trip  # noqa: E402
from _lib.qloo_client import (  # noqa: E402
    QlooAuthError,
    QlooError,
    QlooRateLimited,
    QlooUnavailable,
    QlooClient,
)

MAX_BODY = 20480


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._send(405, {"error": "method_not_allowed", "message": "POST a JSON body: {\"tastes\": [\"...\"]}."})

    def do_POST(self):
        key = os.environ.get("QLOO_API_KEY")
        if not key:
            self._send(503, {
                "error": "qloo_key_not_configured",
                "message": "The service is not connected to the Qloo API yet. Check back shortly.",
            })
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(400, {"error": "bad_request", "message": "Missing or oversized body."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"error": "bad_json", "message": "Body must be valid JSON."})
            return

        try:
            result = plan_trip(QlooClient(key), payload.get("tastes"))
        except PlanError as exc:
            self._send(422, {"error": exc.code, "message": exc.message})
            return
        except QlooAuthError:
            self._send(502, {"error": "upstream_auth", "message": "Qloo rejected the service credential."})
            return
        except QlooRateLimited:
            self._send(429, {"error": "upstream_rate_limited",
                             "message": "Qloo quota is exhausted for now. Try again later."})
            return
        except (QlooUnavailable, QlooError) as exc:
            self._send(502, {"error": "upstream_error", "message": "Qloo call failed: %s" % exc})
            return

        self._send(200, result)
