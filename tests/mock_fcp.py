#!/usr/bin/env python3
"""Mock FreeSocks Control Plane (FCP) for the role's offline tests.

Implements ONLY the bootstrap contract v2 the role calls, with the same
refusals the real FCP applies (FCP docs/servers.md "Node registration"):

  PUT    /api/v1/admin/servers/{slug}/nodes/by-name/{name}            enroll / observe
  GET    /api/v1/admin/servers/{slug}/nodes/by-name/{name}            the node's view
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/bootstrap  machine config + secret
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/applied    applied report
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/wiped      the wipe ack
  DELETE /api/v1/admin/servers/{slug}/nodes/by-name/{name}            retirement request
  GET    /api/v1/admin/status                                          the status gate
  GET    /__state                                                      test hook
  POST   /__mint {token, scopes, boundary?}                            test hook
  POST   /__scenario {name: ...}                                       test hook: what
                                                                       the next calls answer

Scenarios: `plain` (default: a fresh node reconciles at once, bootstrap serves
a secret, applied leads to machine_ready), `blocked` (registration blocked
with a code), `not_set_up` (PUT refused), `stale_revision` (applied refused),
`needs_admin` (DELETE answers a retirement that needs a decision),
`ready_to_wipe` (DELETE answers ready_to_wipe), `unreachable_bootstrap`
(bootstrap answers 502). The secret is only ever in the bootstrap answer.

Run:  python3 tests/mock_fcp.py [port]     (default 8811)
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALL_SCOPES = {
    "admin:servers:read",
    "admin:servers:write",
    "admin:servers:manage",
    "admin:edges:register",
    "admin:status:read",
}
BY_NAME = re.compile(
    r"^/api/v1/admin/servers/(?P<slug>[^/]+)/nodes/by-name/(?P<name>[^/]+)(?:/(?P<verb>bootstrap|applied|wiped))?$"
)
NODE_NAME = re.compile(r"^[A-Za-z0-9 ._-]{3,30}$")
SECRET = "mock-panel-node-secret"

STATE = {
    "scenario": "plain",
    "nodes": {},  # "slug/name" -> intent
    "requests": [],  # [{method, path}]
    "tokens": {},  # token -> {"scopes": [...], "boundary": {...} | None}
    "secrets_served": 0,
}


def _key(slug, name):
    return f"{slug}/{name}"


def _intent(slug, name, body):
    return {
        "name": name,
        "purpose": body.get("purpose"),
        "registration": {"state": "ready", "code": None, "generation": 1},
        "stage": "registered",
        "delivery": "staged",
        "machineRevision": 1,
        "appliedRevision": None,
        "node": {"uuid": "00000001-0000-4000-8000-000000000000", "port": 2222},
        "origin": {
            "hostname": (f"{name}.origin.example" if body.get("purpose") == "front" else None),
            "dns": ("resolves" if body.get("purpose") == "front" else "none"),
        },
        "retirement": None,
        "updatedAt": "2026-09-19T00:00:00.000Z",
        "_observed": body.get("observed"),
        "_label": body.get("label"),
    }


def _view(i):
    return {k: v for k, v in i.items() if not k.startswith("_")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
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

    def _auth(self, scopes, slug=None, name=None):
        """401 without an fsv1_ bearer; 403 outside the token's scopes or boundary."""
        auth = self.headers.get("authorization") or ""
        if not auth.startswith("Bearer fsv1_"):
            self._error(401, "auth.unauthenticated", "missing/invalid fsv1_ bearer token")
            return False
        token = auth[len("Bearer "):]
        reg = STATE["tokens"].get(token)
        granted = set(reg["scopes"]) if reg else ALL_SCOPES
        if not (granted & set(scopes)):
            self._error(403, "auth.forbidden", f"token needs one of: {sorted(scopes)}")
            return False
        # A register-only token is confined to its boundary.
        if reg and "admin:servers:write" not in granted and "admin:servers:manage" not in granted and slug:
            b = reg.get("boundary") or {"backendSlugs": []}
            if slug not in b.get("backendSlugs", []) or (
                b.get("nodeNames") and name not in b["nodeNames"]
            ):
                self._error(403, "servers.registration_boundary", "This token may not act for that node")
                return False
        return True

    def _record(self):
        STATE["requests"].append({"method": self.command, "path": self.path})

    # --- routes -------------------------------------------------------------------

    def do_GET(self):
        self._record()
        if self.path == "/__state":
            return self._send(200, STATE)
        if self.path == "/api/v1/admin/status":
            if not self._auth({"admin:status:read"}):
                return
            return self._send(200, {"users": {}, "backendDrift": 0, "backends": []})
        m = BY_NAME.match(self.path)
        if m and not m.group("verb"):
            if not self._auth({"admin:servers:read", "admin:edges:register", "admin:servers:manage"}, m.group("slug"), m.group("name")):
                return
            i = STATE["nodes"].get(_key(m.group("slug"), m.group("name")))
            if not i:
                return self._error(404, "not_found", "Not found")
            return self._send(200, _view(i))
        self._error(404, "not_found", self.path)

    def do_PUT(self):
        self._record()
        m = BY_NAME.match(self.path)
        if not m or m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name = m.group("slug"), m.group("name")
        if not self._auth({"admin:servers:write", "admin:edges:register", "admin:servers:manage"}, slug, name):
            return
        body = self._body()
        if STATE["scenario"] == "not_set_up":
            return self._error(409, "servers.panel_not_set_up", "Set up this panel in Servers first")
        if int(body.get("roleContractVersion") or 0) < 2:
            return self._error(409, "servers.contract_version", "This panel expects role contract v2")
        if body.get("purpose") not in ("direct", "front", "relay"):
            return self._error(400, "validation", "purpose")
        if not NODE_NAME.match(name):
            return self._error(400, "validation", "name")
        obs = body.get("observed") or {}
        mgmt = obs.get("management") or {}
        if not mgmt.get("address") or not isinstance(mgmt.get("port"), int):
            return self._error(400, "validation", "observed.management")
        i = STATE["nodes"].get(_key(slug, name))
        if i:
            if i["purpose"] != body.get("purpose"):
                return self._error(409, "servers.purpose_change_needs_admin", "A node keeps its purpose")
            if i["retirement"]:
                return self._error(409, "servers.node_retiring", "This node is being retired")
            i["_observed"] = obs
            i["registration"]["generation"] += 1
        else:
            i = _intent(slug, name, body)
            STATE["nodes"][_key(slug, name)] = i
        if STATE["scenario"] == "blocked":
            i["registration"] = {"state": "blocked", "code": "servers.origin_name_taken", "generation": 1}
        return self._send(200, _view(i))

    def do_DELETE(self):
        self._record()
        m = BY_NAME.match(self.path)
        if not m or m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name = m.group("slug"), m.group("name")
        if not self._auth({"admin:servers:write", "admin:edges:register", "admin:servers:manage"}, slug, name):
            return
        i = STATE["nodes"].get(_key(slug, name))
        if not i:
            return self._error(404, "not_found", "Not found")
        stage = "needs_admin" if STATE["scenario"] == "needs_admin" else "ready_to_wipe"
        i["retirement"] = {"stage": stage, "code": None}
        i["delivery"] = "retiring"
        return self._send(200, _view(i))

    def do_POST(self):
        self._record()
        if self.path == "/__mint":
            body = self._body()
            token, scopes = body.get("token"), body.get("scopes")
            if not (isinstance(token, str) and token.startswith("fsv1_") and isinstance(scopes, list)):
                return self._error(400, "validation", "__mint needs {token, scopes}")
            STATE["tokens"][token] = {"scopes": scopes, "boundary": body.get("boundary")}
            return self._send(200, {"ok": True})
        if self.path == "/__scenario":
            STATE["scenario"] = (self._body().get("name") or "plain")
            return self._send(200, {"ok": True, "scenario": STATE["scenario"]})
        m = BY_NAME.match(self.path)
        if not m or not m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name, verb = m.group("slug"), m.group("name"), m.group("verb")
        if not self._auth({"admin:servers:write", "admin:edges:register", "admin:servers:manage"}, slug, name):
            return
        i = STATE["nodes"].get(_key(slug, name))
        if not i:
            return self._error(404, "not_found", "Not found")
        if verb == "bootstrap":
            if STATE["scenario"] == "unreachable_bootstrap":
                return self._error(502, "backend.panel_read_failed", "The panel could not be read")
            STATE["secrets_served"] += 1
            if i["stage"] == "registered":
                i["stage"] = "bootstrap_available"
            ingress = (
                {
                    "hostname": i["origin"]["hostname"],
                    "externalPort": 443,
                    "routes": [{"path": "/ws", "port": 8443}],
                }
                if i["purpose"] == "front"
                else None
            )
            return self._send(200, {
                "machineRevision": i["machineRevision"],
                "secretKey": SECRET,
                "node": {"port": 2222, "name": name, "purpose": i["purpose"]},
                "ingress": ingress,
                "origin": i["origin"],
            })
        if verb == "applied":
            body = self._body()
            rev = int(body.get("appliedRevision") or 0)
            if STATE["scenario"] == "stale_revision" or rev < i["machineRevision"]:
                return self._error(409, "servers.revision_stale", "Run the role again")
            if rev > i["machineRevision"]:
                return self._error(409, "servers.revision_unknown", "Never served")
            if body.get("nodeStarted") is not True:
                return self._error(400, "validation", "nodeStarted")
            repeated = i["appliedRevision"] == rev
            i["appliedRevision"] = rev
            if i["stage"] in ("registered", "bootstrap_available", "machine_applied"):
                i["stage"] = "machine_ready"
            return self._send(200, {"stage": i["stage"], "repeated": repeated})
        if verb == "wiped":
            r = i["retirement"]
            if not r:
                return self._error(409, "servers.not_retiring", "This node is not being retired")
            if r["stage"] not in ("ready_to_wipe", "wiped", "retired"):
                return self._error(409, "servers.retirement_stage", "Not ready to wipe")
            r["stage"] = "retired"
            i["registration"]["state"] = "retired"
            return self._send(200, {"stage": "retired"})
        self._error(404, "not_found", self.path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8811
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
