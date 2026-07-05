"""Local 127.0.0.1:6969 bridge used by the Roblox Studio Lua companion."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Optional, Tuple

from roblox_api import RobloxApiClient, RobloxApiError, normalize_cookie

HOST = "127.0.0.1"
PORT = 6969


def _normalize_ids(raw) -> Dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v).strip() for k, v in raw.items() if str(v).strip()}
    if isinstance(raw, list):
        return {str(i): str(v).strip() for i, v in enumerate(raw) if str(v).strip()}
    return {}


def _normalize_group(value) -> Optional[int]:
    if value in (None, "", False):
        return None
    try:
        group = int(value)
        return group if group > 0 else None
    except (TypeError, ValueError):
        return None


class CopierState:
    def __init__(self, cookie: str = "", group_id: Optional[int] = None, log: Optional[Callable[[str], None]] = None):
        self.lock = threading.RLock()
        self.cookie = normalize_cookie(cookie)
        self.group_id = group_id
        self.log = log or (lambda msg: print(msg, flush=True))
        self.working = True
        self.started = False
        self.finished = False
        self.mapping: Dict[str, str] = {}
        self.failed: Dict[str, str] = {}
        self.last_error = ""
        self.shutdown_requested = False

    def configure(self, cookie: str, group_id: Optional[int] = None) -> None:
        with self.lock:
            self.cookie = normalize_cookie(cookie)
            self.group_id = group_id

    def snapshot(self) -> Tuple[bool, Dict[str, str]]:
        with self.lock:
            return self.finished, dict(self.mapping)

    def log_msg(self, msg: str) -> None:
        self.log(msg)

    def process_payload(self, payload: dict) -> None:
        ids = _normalize_ids(payload.get("ids"))
        payload_cookie = normalize_cookie(payload.get("cookie") or "")
        payload_group_id = _normalize_group(payload.get("groupID") if "groupID" in payload else payload.get("groupId"))
        with self.lock:
            if payload_cookie:
                self.cookie = payload_cookie
            group_id = payload_group_id if payload_group_id is not None else self.group_id
            cookie = self.cookie
            self.started = True
            self.working = True
            self.finished = False
            self.mapping = {}
            self.failed = {}
            self.last_error = ""
        if not ids:
            self.log_msg("No animation IDs received from Studio.")
            with self.lock:
                self.working = False
                self.finished = True
            return
        if not cookie:
            msg = "No .ROBLOSECURITY cookie configured. Paste it in the UI or set myCookie in LuaScript.lua."
            self.log_msg(msg)
            with self.lock:
                self.working = False
                self.finished = True
                self.last_error = msg
            return
        self.log_msg(f"Received {len(ids)} animation id(s). Starting copy flow...")
        try:
            client = RobloxApiClient(cookie, log=self.log_msg)
            client.get_csrf_token()
            mapping: Dict[str, str] = {}
            failed: Dict[str, str] = {}
            for label, old_id in ids.items():
                try:
                    result = client.copy_animation(old_id, group_id=group_id, retries=4)
                    mapping[str(old_id)] = result.new_id
                except Exception as exc:
                    failed[str(old_id)] = str(exc)
                    self.log_msg(f"{label} {old_id} FAILED: {exc}")
                with self.lock:
                    self.mapping = dict(mapping)
                    self.failed = dict(failed)
            self.log_msg("Finished re-uploading animations.")
            if failed:
                self.log_msg("Failed IDs: " + ", ".join(failed.keys()))
        except Exception as exc:
            self.log_msg(f"Copy flow failed: {exc}")
            with self.lock:
                self.last_error = str(exc)
        finally:
            with self.lock:
                self.working = False
                self.finished = True


class RobloxCopierHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, state: CopierState):
        super().__init__(server_address, RequestHandlerClass)
        self.state = state


class CopierRequestHandler(BaseHTTPRequestHandler):
    server_version = "RivvakRobloxCopier/1.0"

    def log_message(self, fmt: str, *args) -> None:
        self.server.state.log_msg("HTTP " + fmt % args)

    def _send_json(self, status: int, data) -> None:
        if status == 204:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
            self.end_headers()
            return
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path not in ("/", "/mapping"):
            self._send_json(404, {"error": "Not found"})
            return
        finished, mapping = self.server.state.snapshot()
        if not finished:
            self._send_json(200, None)
            return
        self._send_json(200, mapping)
        if self.path == "/":
            self.server.state.shutdown_requested = True
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_POST(self):
        if self.path != "/":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return
        self._send_json(204, None)
        threading.Thread(target=self.server.state.process_payload, args=(payload,), daemon=True).start()


class LocalCopierServer:
    def __init__(self, cookie: str = "", group_id: Optional[int] = None, log: Optional[Callable[[str], None]] = None):
        self.state = CopierState(cookie=cookie, group_id=group_id, log=log)
        self.httpd: Optional[RobloxCopierHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self, host: str = HOST, port: int = PORT) -> None:
        if self.httpd:
            return
        self.httpd = RobloxCopierHTTPServer((host, port), CopierRequestHandler, self.state)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.state.log_msg(f"Hosting on http://{host}:{port}")

    def stop(self) -> None:
        if self.httpd:
            self.state.log_msg("Stopping local server...")
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.thread = None

    def configure(self, cookie: str, group_id: Optional[int] = None) -> None:
        self.state.configure(cookie, group_id)


def serve(cookie: str = "", group_id: Optional[int] = None, host: str = HOST, port: int = PORT) -> None:
    server = LocalCopierServer(cookie=cookie, group_id=group_id)
    server.start(host, port)
    try:
        while server.thread and server.thread.is_alive():
            server.thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
