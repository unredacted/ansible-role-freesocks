#!/usr/bin/env python3
"""Mock Cloudflare v4 API for the role's CI integration test.

The role's DNS layer is fully variable-driven (`cloudflare_api_endpoint`), so
pointing it here lets the REAL operation_mode=deploy path run in CI. Implements
just the dns_records surface the role uses (create / list / delete), with the
same envelope Cloudflare returns ({"success", "result", "errors"}), an
in-memory record store, and a bearer-token check.

  POST   /client/v4/zones/{zone}/dns_records         create (A/AAAA)
  GET    /client/v4/zones/{zone}/dns_records?...     list (name/type filters)
  DELETE /client/v4/zones/{zone}/dns_records/{id}    delete
  GET    /__state                                    test hook (records)

Run:  python3 tests/mock_cloudflare.py [port]     (default 8812)
"""

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"records": {}, "next_id": 1, "requests": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[mock-cf] %s\n" % (fmt % args))

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cf(self, status, result, success=True, errors=None):
        self._send(status, {"success": success, "errors": errors or [], "messages": [], "result": result})

    def _authed(self):
        auth = self.headers.get("authorization") or ""
        if not auth.startswith("Bearer ") or len(auth) < 12:
            self._cf(403, None, success=False, errors=[{"code": 9109, "message": "Invalid access token"}])
            return False
        return True

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def do_GET(self):
        STATE["requests"].append({"method": "GET", "path": self.path})
        if self.path == "/__state":
            return self._send(200, STATE)
        if not self._authed():
            return
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"^/client/v4/zones/([^/]+)/dns_records$", parsed.path)
        if m:
            q = urllib.parse.parse_qs(parsed.query)
            records = [r for r in STATE["records"].values() if r["zone"] == m.group(1)]
            if "name" in q:
                records = [r for r in records if r["name"] == q["name"][0]]
            if "type" in q:
                records = [r for r in records if r["type"] == q["type"][0]]
            return self._cf(200, records)
        self._cf(404, None, success=False, errors=[{"code": 7003, "message": "no route"}])

    def do_POST(self):
        STATE["requests"].append({"method": "POST", "path": self.path})
        if not self._authed():
            return
        m = re.match(r"^/client/v4/zones/([^/]+)/dns_records$", self.path)
        if not m:
            return self._cf(404, None, success=False, errors=[{"code": 7003, "message": "no route"}])
        body = self._body()
        if body.get("type") not in ("A", "AAAA", "CNAME"):
            return self._cf(400, None, success=False, errors=[{"code": 9004, "message": "invalid type"}])
        if not body.get("name") or not body.get("content"):
            return self._cf(400, None, success=False, errors=[{"code": 9005, "message": "name/content required"}])
        rid = f"cfrec{STATE['next_id']:06d}"
        STATE["next_id"] += 1
        rec = {
            "id": rid,
            "zone": m.group(1),
            "type": body["type"],
            "name": body["name"],
            "content": body["content"],
            "proxied": bool(body.get("proxied", False)),
            "ttl": body.get("ttl", 1),
        }
        STATE["records"][rid] = rec
        return self._cf(200, rec)

    def do_DELETE(self):
        STATE["requests"].append({"method": "DELETE", "path": self.path})
        if not self._authed():
            return
        m = re.match(r"^/client/v4/zones/([^/]+)/dns_records/([^/]+)$", self.path)
        if not m:
            return self._cf(404, None, success=False, errors=[{"code": 7003, "message": "no route"}])
        rec = STATE["records"].pop(m.group(2), None)
        if rec is None:
            return self._cf(404, None, success=False, errors=[{"code": 81044, "message": "Record not found"}])
        return self._cf(200, {"id": m.group(2)})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8812
    print(f"[mock-cf] listening on 127.0.0.1:{port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
