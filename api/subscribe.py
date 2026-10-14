"""Newsletter signup: POST /api/subscribe

Env:
  RESEND_API_KEY      required. Resend key that can create contacts.
  RESEND_SEGMENT_ID   optional. When set, the contact is added to this segment.
                      When unset, the contact is created on the global list.

Creates a contact with POST https://api.resend.com/contacts
(segments: [{id}] when RESEND_SEGMENT_ID is set). A duplicate contact is treated
as success and, when a segment is configured, added with
POST /contacts/{email}/segments/{segment_id}.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

ALLOWED_ORIGINS = {
    "https://stazgames.com",
    "https://www.stazgames.com",
    "https://fermartinez430.github.io",
}
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
CONTACTS_URL = "https://api.resend.com/contacts"
WINDOW = 600
LIMIT = 5
OK_MSG = "You're in. Watch your inbox in the morning."
ALREADY_MSG = "You're already on the list."
INVALID_MSG = "Enter a valid email address."
RATE_MSG = "Too many attempts. Please try again in a few minutes."
UNAVAILABLE = "Signup is temporarily unavailable."
GENERIC = "Something went wrong. Please try again."

_hits = {}
_lock = threading.Lock()


def reset_limits():
    with _lock:
        _hits.clear()


def origin_allowed(origin):
    """Production sites, plus localhost so `vercel dev` can post to the function."""
    if not origin:
        return True
    if origin in ALLOWED_ORIGINS:
        return True
    parsed = urllib.parse.urlparse(origin)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}


def cors_headers(origin):
    headers = {
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Accept",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }
    if origin and origin_allowed(origin):
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def too_many(ip, now):
    with _lock:
        prev = [t for t in _hits.get(ip, []) if now - t < WINDOW]
        if len(prev) >= LIMIT:
            _hits[ip] = prev
            return True
        prev.append(now)
        _hits[ip] = prev[-LIMIT:]
        return False


def valid_email(value):
    email = str(value or "").strip().lower()
    if not email or len(email) > 254 or email.count("@") != 1:
        return ""
    local, _, domain = email.partition("@")
    if len(local) > 64 or not domain or ".." in email:
        return ""
    if any(c in email for c in " \t\r\n"):
        return ""
    if not EMAIL_RE.fullmatch(email):
        return ""
    return email


def parse_payload(raw, content_type):
    if not raw:
        return {}
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    text = raw.decode("utf-8")
    if ctype == "application/json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True, max_num_fields=20)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def post_resend(url, payload, api_key, idempotency_key):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + api_key,
        "Accept": "application/json",
        "User-Agent": "StazGames/1.0",
        "Idempotency-Key": idempotency_key[:256],
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return resp.status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                body = {"message": str(body)}
        except json.JSONDecodeError:
            body = {"message": raw[:300]}
        return exc.code, body


def already_contact(body):
    msg = " ".join(str(body.get(k) or "") for k in ("message", "name", "error")).lower()
    return "already exists" in msg or ("already" in msg and "contact" in msg)


def process_subscribe(raw, content_type, *, origin="", ip="unknown", env=None, now=None, poster=None):
    """Validate a signup and create the Resend contact. Returns (status, json)."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    poster = post_resend if poster is None else poster
    if not origin_allowed(origin):
        return 403, {"ok": False, "error": "This origin is not allowed."}
    try:
        data = parse_payload(raw, content_type)
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return 400, {"ok": False, "error": INVALID_MSG}
    honeypot = str(data.get("hp") or data.get("company") or "").strip()
    if honeypot:
        return 200, {"ok": True, "message": OK_MSG}
    if too_many(ip or "unknown", now):
        return 429, {"ok": False, "error": RATE_MSG}
    email = valid_email(data.get("email"))
    if not email:
        return 400, {"ok": False, "error": INVALID_MSG}
    api_key = str(env.get("RESEND_API_KEY") or "").strip()
    segment = str(env.get("RESEND_SEGMENT_ID") or "").strip()
    if not api_key:
        print("RESEND_API_KEY is not set", file=sys.stderr)
        return 503, {"ok": False, "error": UNAVAILABLE}
    if segment and not SEGMENT_RE.fullmatch(segment):
        print("RESEND_SEGMENT_ID is not a valid id", file=sys.stderr)
        return 503, {"ok": False, "error": UNAVAILABLE}
    payload = {"email": email, "unsubscribed": False}
    if segment:
        payload["segments"] = [{"id": segment}]
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    try:
        status, body = poster(CONTACTS_URL, payload, api_key, "subscribe/" + digest)
    except Exception as exc:
        print(f"resend request failed: {type(exc).__name__}", file=sys.stderr)
        return 502, {"ok": False, "error": GENERIC}
    if status in (200, 201):
        return 200, {"ok": True, "message": OK_MSG}
    if already_contact(body):
        if segment:
            seg_url = CONTACTS_URL + "/" + urllib.parse.quote(email, safe="") + "/segments/" + urllib.parse.quote(segment, safe="")
            try:
                poster(seg_url, None, api_key, f"subscribe-segment/{digest}/{segment}")
            except Exception as exc:
                print(f"resend segment failed: {type(exc).__name__}", file=sys.stderr)
        return 200, {"ok": True, "message": ALREADY_MSG}
    if status == 429:
        return 429, {"ok": False, "error": RATE_MSG}
    print(f"resend contacts {status} {body.get('name', '')}", file=sys.stderr)
    if status in (401, 403):
        return 503, {"ok": False, "error": UNAVAILABLE}
    return 502, {"ok": False, "error": GENERIC}


class handler(BaseHTTPRequestHandler):
    def _client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() or self.headers.get("X-Real-IP", "")
        if ip:
            return ip
        return self.client_address[0] if self.client_address else "unknown"

    def _finish(self, status, payload):
        origin = self.headers.get("Origin", "")
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in cors_headers(origin).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        self.send_response(204)
        for key, value in cors_headers(origin).items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._finish(405, {"ok": False, "error": "Send a POST request with an email address."})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > 10000:
            self._finish(413, {"ok": False, "error": "That request is too large."})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            status, payload = process_subscribe(
                raw,
                self.headers.get("Content-Type", ""),
                origin=self.headers.get("Origin", ""),
                ip=self._client_ip(),
            )
        except Exception as exc:
            print(f"subscribe failed: {type(exc).__name__}", file=sys.stderr)
            status, payload = 500, {"ok": False, "error": GENERIC}
        self._finish(status, payload)

    def log_message(self, fmt, *args):
        return
