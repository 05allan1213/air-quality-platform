"""Internal synthetic HTTP service for the S03 Compose verifier only."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PATHS = {
    "/CityData/GetAllCityRealTimeAQIModels": ("GET", "L", None),
    "/CityData/GetAQIDataPublishLiveInfo": ("POST", "C", "cityCode"),
    "/CityData/GetAQIDataPublishLive": ("GET", "S", "cityName"),
    "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition": ("POST", "T", "citycode"),
    "/synthetic-redirect-target": ("GET", "redirect", None),
}
CNEMC_PATHS = {
    **dict(zip(tuple(PATHS)[:4], ("A01", "A03", "A04", "A05"), strict=True)),
    "/HourChangesPublish/GetAllAQIPublishLive": "A07",
    "/HourChangesPublish/GetAQIHistoryByConditionHis": "A10",
}
PATHS.update(
    {
        "/HourChangesPublish/GetAllAQIPublishLive": ("POST", "A07", None),
        "/HourChangesPublish/GetAQIHistoryByConditionHis": ("POST", "A10", None),
    }
)
STATE = threading.Condition()
SCRIPT = {}
EVENTS = []
RELEASED = set()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def json(self, body, status=200):
        self.respond(status, json.dumps(body).encode())

    def respond(self, status, body, headers=None, script=None):
        script = script or {}
        self.send_response(status)
        headers = {"Content-Type": "application/json", **(headers or {})}
        if script.get("omit_length"):
            headers["Connection"] = "close"
            self.close_connection = True
        else:
            headers["Content-Length"] = str(len(body) + script.get("truncate", 0))
            if script.get("truncate"):
                headers["Connection"] = "close"
                self.close_connection = True
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        chunk_size = script.get("chunk_size") or len(body) or 1
        try:
            for pos in range(0, len(body), chunk_size):
                self.wfile.write(body[pos : pos + chunk_size])
                self.wfile.flush()
                if pos + chunk_size < len(body):
                    time.sleep(script.get("chunk_delay", 0))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def control(self, path):
        if self.command == "GET" and path == "/_events":
            with STATE:
                events = list(EVENTS)
            self.json(events)
            return True
        if self.command == "POST" and path in {"/_script", "/_release"}:
            count = int(self.headers.get("Content-Length", 0))
            if not 0 < count <= 8 * 2**20:
                self.json({}, 400)
                return True
            payload = json.loads(self.rfile.read(count))
            with STATE:
                if path == "/_script":
                    SCRIPT.update(payload)
                else:
                    RELEASED.add(payload["gate"])
                    STATE.notify_all()
            self.json({"ok": True})
            return True
        return False

    def route(self):
        url = urlsplit(self.path)
        if self.control(url.path):
            return
        if url.path not in PATHS:
            self.json({}, 404)
            return
        method, shape, query_key = PATHS[url.path]
        params = parse_qs(url.query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length", "0"))
        form = {}
        if shape == "A10" and 0 < length <= 128:
            form = parse_qs(self.rfile.read(length).decode("ascii"), keep_blank_values=True)
        valid = (
            self.command == method
            and set(params) == ({query_key} if query_key else set())
            and all(len(v) == 1 and v[0] for v in params.values())
            and (
                self.headers.get("Content-Type") == "application/x-www-form-urlencoded"
                and set(form) == {"date"}
                and len(form["date"]) == 1
                and bool(form["date"][0])
                if shape == "A10"
                else length == 0
            )
        )
        with STATE:
            if "X-S03-Target" not in self.headers and (endpoint := CNEMC_PATHS.get(url.path)):
                # S03 latest retains its discriminator when no CNEMC event is scripted.
                if shape == endpoint or endpoint in SCRIPT:
                    shape = endpoint
            queue = SCRIPT.get(shape) or []
            script = queue.pop(0) if queue else {"status": 503}
            status = script.get("status", 200) if valid else 400
            EVENTS.append(
                {
                    "at_ns": time.time_ns(),
                    "method": self.command,
                    "shape": shape,
                    "keys": sorted(params),
                    "target": params.get(query_key),
                    "window": self.headers.get("X-S03-Target"),
                    "status": status,
                    "gate": script.get("gate"),
                    "empty_body": length == 0,
                    "form": form,
                    "encoding": self.headers.get("Accept-Encoding"),
                }
            )
            if valid and (gate := script.get("gate")):
                if not STATE.wait_for(lambda: gate in RELEASED, timeout=45):
                    status = 504
        if delay := script.get("delay"):
            time.sleep(delay)
        body = (
            script["raw"].encode() if "raw" in script else json.dumps(script.get("body")).encode()
        )
        self.respond(status, body, script.get("headers"), script)

    do_GET = route
    do_POST = route


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
