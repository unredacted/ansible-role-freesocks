#!/usr/bin/env python3
"""Mock FreeSocks Control Plane (FCP) admin API for the role's CI integration test.

Implements ONLY the admin surface the role calls, with the same strictness the
real FCP applies, so a contract regression in the role fails here instead of in
production:

  PUT    /api/v1/admin/backend-servers/by-slug/{slug}   (keep-secret-on-blank upsert)
  DELETE /api/v1/admin/backend-servers/by-slug/{slug}   (idempotent)
  POST   /api/v1/admin/backend-servers/test-connection  (STRICT: rejects undeclared
                                                         fields like name/maxKeys/priority,
                                                         mirroring Convex arg validators)
  PATCH  /api/v1/admin/remnawave/mode-placements        (squadUuids replace +
                                                         addSquadUuids/removeSquadUuids;
                                                         UUID-validated like FCP)
  PATCH  /api/v1/admin/connection-modes                 ({default} only)
  GET    /api/v1/admin/status                           (registered slugs, healthy)
  GET    /__state                                       (test hook: full mock state)
  POST   /__mint {token, scopes}                        (test hook: register a
                                                         restricted token)

Every request must carry `Authorization: Bearer fsv1_...` or it 401s, mirroring
resolveAdmin. Each route also enforces its real FCP scope (403 auth.forbidden):
by-slug PUT/DELETE + mode-placements need admin:servers:write; test-connection
and node-stats need admin:servers:read; connection-modes needs
admin:settings:write; status needs admin:status:read. Tokens registered via
/__mint carry only their listed scopes; unregistered fsv1_ tokens get all
scopes (backward compatible). Errors use FCP's {"error": {"code", "message"}}
envelope.

Run:  python3 tests/mock_fcp.py [port]     (default 8811)
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MODE_IDS = ("evade", "privacy")
TESTCONN_FIELDS = {"backend", "id", "baseUrl", "apiToken", "apiUrl", "websocketEnabled", "websocketDomain"}
ALL_SCOPES = {"admin:servers:read", "admin:servers:write", "admin:settings:write", "admin:status:read"}

STATE = {
    "servers": {},        # slug -> row
    "pools": {},          # modeId -> [squadUuid]
    "defaultMode": None,
    "requests": [],       # [{method, path}] audit trail for assertions
    "tokens": {},         # token -> [scopes] (only /__mint-registered; others get ALL_SCOPES)
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        sys.stderr.write("[mock-fcp] %s\n" % (fmt % args))

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status, code, message):
        self._send(status, {"error": {"code": code, "message": message}})

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

    def _scoped(self, scope):
        """401 without an fsv1_ bearer; 403 when the token lacks the route's scope."""
        auth = self.headers.get("authorization") or ""
        if not auth.startswith("Bearer fsv1_"):
            self._error(401, "auth.unauthenticated", "missing/invalid fsv1_ bearer token")
            return False
        token = auth[len("Bearer "):]
        granted = STATE["tokens"].get(token, ALL_SCOPES)
        if scope not in granted:
            self._error(403, "auth.forbidden", f"token missing required scope: {scope}")
            return False
        return True

    def _record(self):
        STATE["requests"].append({"method": self.command, "path": self.path})

    # --- routes ---------------------------------------------------------------

    def do_GET(self):
        self._record()
        if self.path == "/__state":
            return self._send(200, STATE)
        if self.path == "/api/v1/admin/status":
            if not self._scoped("admin:status:read"):
                return
            return self._send(200, {
                "users": {"active": 0, "grace": 0, "disabled": 0, "deleted": 0, "inactive": 0},
                "backendDrift": 0,
                "backends": [
                    {"slug": s, "backend": r["backend"], "isActive": True, "keyCount": 0,
                     "healthy": True, "lastHealthOkAt": None, "lastHealthRttMs": None,
                     "fleetStats": None}
                    for s, r in STATE["servers"].items()
                ],
            })
        if self.path == "/api/v1/admin/remnawave/node-stats":
            if not self._scoped("admin:servers:read"):
                return
            return self._send(200, {
                "nodes": [],
                "placements": [{"modeId": m, "boundCount": len(STATE["pools"].get(m, []))}
                               for m in MODE_IDS],
            })
        self._error(404, "not_found", self.path)

    def do_PUT(self):
        self._record()
        if not self._scoped("admin:servers:write"):
            return
        m = re.match(r"^/api/v1/admin/backend-servers/by-slug/(.+)$", self.path)
        if not m:
            return self._error(404, "not_found", self.path)
        slug = m.group(1)
        body = self._body()
        backend = body.get("backend")
        if backend not in ("remnawave", "outline"):
            return self._error(400, "validation", "backend must be remnawave|outline")
        existing = STATE["servers"].get(slug)
        if existing and existing["backend"] != backend:
            return self._error(400, "admin.error", f'exists as type "{existing["backend"]}"')
        if not existing:
            if backend == "remnawave" and not (body.get("baseUrl") and body.get("apiToken")):
                return self._error(400, "admin.error", "A Remnawave instance needs a base URL and an API token")
            if backend == "outline" and not body.get("apiUrl"):
                return self._error(400, "admin.error", "An Outline instance needs an apiUrl")
        mk = body.get("maxKeys")
        if mk is not None and "maxKeys" in body and not (isinstance(mk, int) and mk >= 1):
            return self._error(400, "admin.error", "maxKeys must be a positive integer (or null to clear the cap)")
        row = dict(existing or {})
        # keep-secret-on-blank: only non-empty strings overwrite
        for k in ("baseUrl", "apiToken", "apiUrl"):
            if isinstance(body.get(k), str) and body[k]:
                row[k] = body[k]
        for k in ("name", "isActive", "priority", "websocketEnabled", "websocketDomain", "maxKeys"):
            if k in body:
                row[k] = body[k]
        row["backend"] = backend
        STATE["servers"][slug] = row
        return self._send(200, {"id": "srv_" + slug, "slug": slug, "backend": backend,
                                "created": existing is None})

    def do_DELETE(self):
        self._record()
        if not self._scoped("admin:servers:write"):
            return
        m = re.match(r"^/api/v1/admin/backend-servers/by-slug/(.+)$", self.path)
        if not m:
            return self._error(404, "not_found", self.path)
        existed = STATE["servers"].pop(m.group(1), None) is not None
        return self._send(200, {"ok": True, "deleted": existed})

    def do_POST(self):
        self._record()
        # Test hook (unauthenticated, like /__state): register a restricted token.
        if self.path == "/__mint":
            body = self._body()
            token = body.get("token")
            scopes = body.get("scopes")
            if not (isinstance(token, str) and token.startswith("fsv1_") and isinstance(scopes, list)):
                return self._error(400, "validation", "__mint needs {token: 'fsv1_...', scopes: [...]}")
            bad = [s for s in scopes if s not in ALL_SCOPES]
            if bad:
                return self._error(400, "validation", f"unknown scopes: {bad}")
            STATE["tokens"][token] = list(scopes)
            return self._send(200, {"ok": True})
        if not self._scoped("admin:servers:read"):
            return
        if self.path == "/api/v1/admin/backend-servers/test-connection":
            body = self._body()
            # Mirror Convex arg-validator strictness: an undeclared field is an
            # error (this is what broke the probe when it carried `name`).
            extra = set(body) - TESTCONN_FIELDS
            if extra:
                return self._error(400, "validation", f"undeclared fields: {sorted(extra)}")
            if body.get("backend") not in ("remnawave", "outline"):
                return self._error(400, "validation", "backend must be remnawave|outline")
            return self._send(200, {"ok": True, "keyCount": 0})
        self._error(404, "not_found", self.path)

    def do_PATCH(self):
        self._record()
        if self.path == "/api/v1/admin/remnawave/mode-placements":
            if not self._scoped("admin:servers:write"):
                return
            body = self._body()
            modes = body.get("modes")
            if not isinstance(modes, dict):
                return self._error(400, "validation", "mode-placement patch must be an object")
            wrote = False
            for mode_id, entry in modes.items():
                if mode_id not in MODE_IDS or not isinstance(entry, dict):
                    continue
                replace = entry.get("squadUuids")
                add = entry.get("addSquadUuids")
                remove = entry.get("removeSquadUuids")
                if replace is None and add is None and remove is None:
                    continue
                # FCP validates replace/add entries as UUIDs; remove is any string.
                for field, val, strict in (("squadUuids", replace, True),
                                           ("addSquadUuids", add, True),
                                           ("removeSquadUuids", remove, False)):
                    if val is None:
                        continue
                    if not isinstance(val, list) or any(not isinstance(s, str) or not s.strip() for s in val):
                        return self._error(400, "validation", f"{field} must be an array of non-empty strings")
                    if strict:
                        bad = [s for s in val if not UUID_RE.match(s.strip())]
                        if bad:
                            return self._error(400, "validation", "not a squad UUID: " + ", ".join(bad))
                pool = list(replace) if replace is not None else list(STATE["pools"].get(mode_id, []))
                if add:
                    pool += [s for s in add if s not in pool]
                if remove:
                    pool = [s for s in pool if s not in set(remove)]
                # dedupe, keep order
                seen, deduped = set(), []
                for s in pool:
                    if s not in seen:
                        seen.add(s)
                        deduped.append(s)
                STATE["pools"][mode_id] = deduped
                wrote = True
            if not wrote:
                return self._error(400, "validation", "no recognized mode-placement fields")
            return self._send(200, {
                "bound": [m for m in MODE_IDS if STATE["pools"].get(m)],
                "placements": [{"modeId": m, "boundCount": len(STATE["pools"].get(m, []))}
                               for m in MODE_IDS],
            })
        if self.path == "/api/v1/admin/connection-modes":
            if not self._scoped("admin:settings:write"):
                return
            body = self._body()
            default = body.get("default")
            if default is not None and default not in MODE_IDS:
                return self._error(400, "validation", "invalid default mode id")
            if default is None and not body.get("modes"):
                return self._error(400, "validation", "no recognized connection-mode fields")
            if default is not None:
                STATE["defaultMode"] = default
            return self._send(200, {"modes": [
                {"id": m, "label": None, "description": None,
                 "deliveryStyle": "url" if m == "evade" else "rawConfig",
                 "isDefault": (STATE["defaultMode"] or "evade") == m,
                 "bound": bool(STATE["pools"].get(m))}
                for m in MODE_IDS
            ]})
        self._error(404, "not_found", self.path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8811
    print(f"[mock-fcp] listening on 127.0.0.1:{port}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
