#!/usr/bin/env python3
"""Mock FreeSocks Control Plane (FCP) for the role's offline tests.

Implements ONLY the bootstrap contract v2 the role calls, with the same
refusals the real FCP applies (FCP docs/servers.md "Node registration",
"Firewall acknowledgement", "Renaming a node"):

  PUT    /api/v1/admin/servers/{slug}/nodes/by-name/{name}            enroll / observe
  GET    /api/v1/admin/servers/{slug}/nodes/by-name/{name}            the node's view
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/bootstrap  machine config + secret
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/applied    applied report (+ firewall)
  POST   /api/v1/admin/servers/{slug}/nodes/by-name/{name}/wiped      the wipe ack
  DELETE /api/v1/admin/servers/{slug}/nodes/by-name/{name}            retirement request
  GET    /api/v1/admin/status                                          the status gate
  GET    /__state                                                      test hook
  POST   /__mint {token, scopes, boundary?}                            test hook
  POST   /__scenario {name: ...}                                       test hook: what
                                                                       the next calls answer
  POST   /__port_change {slug, name, port}                             test hook: stage a
                                                                       port change on a node
  POST   /__settle {id}                                                test hook: the change
                                                                       was sent and is seen
  POST   /__rename {slug, from, to}                                    test hook: an admin
                                                                       renamed the node

Scope rules (convex/httpServers.ts `scopeFor`, lib/adminHttp.ts): a by-name GET
takes admin:servers:read, admin:servers:write, admin:edges:register or
admin:servers:manage; every other verb takes write, register or manage. A token
holding the verb's FULL scope (GET: read or write; else write) is unbounded;
every other one (register, and manage too) is confined to its boundary, and a
token with no boundary may act for nothing (403 servers.registration_boundary),
so backend-write automation never reaches the node secret on its own. A token
without any accepted scope gets 401. The boundary is checked against the name
in the path, as FCP does.

The node's view and the bootstrap answer carry `firewall.ports`: the port its
transport serves (443; a WebSocket node's loopback transport is not listed, its
Caddy's 443 is) as `serving`, and the port of every change staged for it and
not yet settled as `pending`, confirmed or not. An applied report with
`firewall.ports` confirms this node's waiting changes whose port it names; one
without `firewall` confirms nothing.

A renamed node answers under its old name too (an alias), its view's `name` is
the new one, and a token bounded to this backend alone gains the new name.

Scenarios: `plain` (default: a fresh node reconciles at once, bootstrap serves
a secret, applied leads to machine_ready), `blocked` (registration blocked
with a code), `not_set_up` (PUT refused), `stale_revision` (applied refused),
`needs_admin` (DELETE answers a retirement that needs a decision),
`ready_to_wipe` (DELETE answers ready_to_wipe), `unreachable_bootstrap`
(bootstrap answers 502), `blocked_later` (the node's view turns blocked, as
when FCP cannot verify the applied machine), `not_ready` (applied leaves the
node at machine_applied), `old_fcp` (no `firewall` in the view or the bootstrap
answer, as before the firewall acknowledgement). The secret is only ever in
the bootstrap answer.

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
# What the node role's routes accept, and which of it is unbounded.
BY_NAME_ACCEPTS = {
    "GET": {"admin:servers:read", "admin:servers:write", "admin:edges:register", "admin:servers:manage"},
    "WRITE": {"admin:servers:write", "admin:edges:register", "admin:servers:manage"},
}
BY_NAME_UNBOUNDED = {
    "GET": {"admin:servers:read", "admin:servers:write"},
    "WRITE": {"admin:servers:write"},
}
BY_NAME = re.compile(
    r"^/api/v1/admin/servers/(?P<slug>[^/]+)/nodes/by-name/(?P<name>[^/]+)(?:/(?P<verb>bootstrap|applied|wiped))?$"
)
NODE_NAME = re.compile(r"^[A-Za-z0-9 ._-]{3,30}$")
SECRET = "mock-panel-node-secret"

STATE = {
    "scenario": "plain",
    "nodes": {},  # "slug/name" -> intent
    "aliases": {},  # "slug/former" -> "slug/current"
    "requests": [],  # [{method, path}]
    "tokens": {},  # token -> {"scopes": [...], "boundary": {...} | None}
    "secrets_served": 0,
    "port_changes": [],  # [{id, key, port, state: waiting|confirmed|settled}]
    "applied_reports": [],  # [{name, firewall}]
    "confirmations": [],  # [{id, name, port}]
}


def _key(slug, name):
    return f"{slug}/{name}"


def _find(slug, name):
    """The node under its name, or under a name it had before a rename."""
    key = _key(slug, name)
    key = STATE["aliases"].get(key, key)
    return STATE["nodes"].get(key), key


# The modes a backend is set up for, as FCP would answer them (shape decides the machine).
MODES = {
    "privacy-reality": {"slug": "privacy-reality", "name": "Privacy-Reality", "shape": {"transport": "reality", "fronting": "direct"}},
    "freedom-reality": {"slug": "freedom-reality", "name": "Freedom-Reality", "shape": {"transport": "reality", "fronting": "edge-l4"}},
    "freedom-xhttp": {"slug": "freedom-xhttp", "name": "Freedom-XHTTP", "shape": {"transport": "xhttp-reality", "fronting": "edge-l4"}},
    "freedom-ws": {"slug": "freedom-ws", "name": "Freedom-WebSocket", "shape": {"transport": "ws", "fronting": "edge-l7"}},
}


def _is_ws(mode):
    return MODES[mode]["shape"]["transport"] == "ws"


def _intent(slug, name, body):
    mode = body.get("mode")
    return {
        "name": name,
        "mode": MODES[mode],
        "registration": {"state": "ready", "code": None, "generation": 1},
        "stage": "registered",
        "delivery": "staged",
        "machineRevision": 1,
        "appliedRevision": None,
        "node": {"uuid": "00000001-0000-4000-8000-000000000000", "port": 2222},
        "origin": {
            "hostname": (f"{name}.origin.example" if _is_ws(mode) else None),
            "dns": ("resolves" if _is_ws(mode) else "none"),
        },
        "retirement": None,
        "updatedAt": "2026-09-19T00:00:00.000Z",
        "_slug": slug,
        # A REALITY transport listens on its public port; a WebSocket one on
        # loopback behind Caddy, whose public port is what the firewall serves.
        "_serving": 443,
        "_observed": body.get("observed"),
        "_label": body.get("label"),
    }


def _firewall(i, key):
    serving = {i["_serving"]}
    pending = {
        c["port"]
        for c in STATE["port_changes"]
        if c["key"] == key and c["state"] in ("waiting", "confirmed") and c["port"] not in serving
    }
    return {
        "ports": [{"port": p, "protocol": "tcp", "reason": "serving"} for p in sorted(serving)]
        + [{"port": p, "protocol": "tcp", "reason": "pending"} for p in sorted(pending)]
    }


def _view(i, key):
    out = {k: v for k, v in i.items() if not k.startswith("_")}
    if STATE["scenario"] != "old_fcp":
        out["firewall"] = _firewall(i, key)
    return out


def _firewall_report(body):
    """The applied report's `firewall`: None when absent, else its ports; raises on a bad shape."""
    if "firewall" not in body:
        return None
    fw = body["firewall"]
    if not isinstance(fw, dict) or not isinstance(fw.get("ports"), list) or len(fw["ports"]) > 64:
        raise ValueError("firewall")
    ports = []
    for p in fw["ports"]:
        if not isinstance(p, dict) or p.get("protocol") != "tcp":
            raise ValueError("firewall.ports.protocol")
        port = p.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("firewall.ports.port")
        ports.append(port)
    return ports


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

    def _token(self):
        auth = self.headers.get("authorization") or ""
        if not auth.startswith("Bearer fsv1_"):
            return None, None
        token = auth[len("Bearer "):]
        reg = STATE["tokens"].get(token)
        # An unminted test token holds every scope (the fleet token and more).
        return token, (reg if reg else {"scopes": sorted(ALL_SCOPES), "boundary": None})

    def _auth(self, scopes):
        """401 without an fsv1_ bearer holding one of the scopes (FCP answers 401 for both)."""
        token, reg = self._token()
        if not token or not (set(reg["scopes"]) & set(scopes)):
            self._error(401, "auth.unauthenticated", "Authentication required")
            return False
        return True

    def _auth_by_name(self, slug, name):
        """The node role's routes: the verb's scopes, then the registration boundary."""
        kind = "GET" if self.command == "GET" else "WRITE"
        if not self._auth(BY_NAME_ACCEPTS[kind]):
            return False
        _, reg = self._token()
        if set(reg["scopes"]) & BY_NAME_UNBOUNDED[kind]:
            return True
        b = reg.get("boundary") or {"backendSlugs": []}
        if slug not in b.get("backendSlugs", []) or (b.get("nodeNames") and name not in b["nodeNames"]):
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
            slugs = sorted({i["_slug"] for i in STATE["nodes"].values()})
            return self._send(200, {"users": {}, "backendDrift": 0, "backends": [{"slug": s, "healthy": True} for s in slugs]})
        m = BY_NAME.match(self.path)
        if m and not m.group("verb"):
            if not self._auth_by_name(m.group("slug"), m.group("name")):
                return
            i, key = _find(m.group("slug"), m.group("name"))
            if not i:
                return self._error(404, "not_found", "Not found")
            if STATE["scenario"] == "blocked_later":
                # FCP blocked the node while verifying the applied machine.
                i["registration"] = {**i["registration"], "state": "blocked", "code": "servers.node_offline"}
            return self._send(200, _view(i, key))
        self._error(404, "not_found", self.path)

    def do_PUT(self):
        self._record()
        m = BY_NAME.match(self.path)
        if not m or m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name = m.group("slug"), m.group("name")
        if not self._auth_by_name(slug, name):
            return
        body = self._body()
        if STATE["scenario"] == "not_set_up":
            return self._error(409, "servers.panel_not_set_up", "Set up this panel in Servers first")
        if int(body.get("roleContractVersion") or 0) < 2:
            return self._error(409, "servers.contract_version", "This panel expects role contract v2")
        if body.get("mode") not in MODES:
            return self._error(409, "servers.mode_unknown", "This backend is not set up for that mode")
        if not NODE_NAME.match(name):
            return self._error(400, "validation", "name")
        obs = body.get("observed") or {}
        mgmt = obs.get("management") or {}
        if not mgmt.get("address") or not isinstance(mgmt.get("port"), int):
            return self._error(400, "validation", "observed.management")
        i, key = _find(slug, name)
        if i:
            if i["mode"]["slug"] != body.get("mode"):
                return self._error(409, "servers.mode_change_needs_admin", "A node keeps its mode")
            if i["retirement"]:
                return self._error(409, "servers.node_retiring", "This node is being retired")
            i["_observed"] = obs
            i["registration"]["generation"] += 1
        else:
            i = _intent(slug, name, body)
            STATE["nodes"][key] = i
        if STATE["scenario"] == "blocked":
            i["registration"] = {"state": "blocked", "code": "servers.origin_name_taken", "generation": 1}
        return self._send(200, _view(i, key))

    def do_DELETE(self):
        self._record()
        m = BY_NAME.match(self.path)
        if not m or m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name = m.group("slug"), m.group("name")
        if not self._auth_by_name(slug, name):
            return
        i, key = _find(slug, name)
        if not i:
            return self._error(404, "not_found", "Not found")
        stage = "needs_admin" if STATE["scenario"] == "needs_admin" else "ready_to_wipe"
        i["retirement"] = {"stage": stage, "code": None}
        i["delivery"] = "retiring"
        return self._send(200, _view(i, key))

    def _hooks(self):
        """Test hooks; True when the request was one."""
        if self.path == "/__mint":
            body = self._body()
            token, scopes = body.get("token"), body.get("scopes")
            if not (isinstance(token, str) and token.startswith("fsv1_") and isinstance(scopes, list)):
                self._error(400, "validation", "__mint needs {token, scopes}")
                return True
            STATE["tokens"][token] = {"scopes": scopes, "boundary": body.get("boundary")}
            self._send(200, {"ok": True})
            return True
        if self.path == "/__scenario":
            STATE["scenario"] = (self._body().get("name") or "plain")
            self._send(200, {"ok": True, "scenario": STATE["scenario"]})
            return True
        if self.path == "/__port_change":
            body = self._body()
            i, key = _find(body.get("slug"), body.get("name"))
            if not i:
                self._error(404, "not_found", "no such node")
                return True
            change = {"id": len(STATE["port_changes"]) + 1, "key": key, "port": int(body["port"]), "state": "waiting"}
            STATE["port_changes"].append(change)
            self._send(200, change)
            return True
        if self.path == "/__settle":
            cid = int(self._body().get("id") or 0)
            for c in STATE["port_changes"]:
                if c["id"] == cid:
                    c["state"] = "settled"
                    STATE["nodes"][c["key"]]["_serving"] = c["port"]
                    self._send(200, c)
                    return True
            self._error(404, "not_found", "no such change")
            return True
        if self.path == "/__rename":
            body = self._body()
            slug, old, new = body.get("slug"), body.get("from"), body.get("to")
            i, old_key = _find(slug, old)
            if not i:
                self._error(404, "not_found", "no such node")
                return True
            new_key = _key(slug, new)
            STATE["nodes"][new_key] = STATE["nodes"].pop(old_key)
            i["name"] = new
            for former, current in list(STATE["aliases"].items()):
                if current == old_key:
                    STATE["aliases"][former] = new_key
            STATE["aliases"][old_key] = new_key
            for c in STATE["port_changes"]:
                if c["key"] == old_key:
                    c["key"] = new_key
            # A token bounded to this backend alone gains the new name; one
            # spanning several backends keeps acting under the old name only.
            for reg in STATE["tokens"].values():
                b = reg.get("boundary") or {}
                if b.get("backendSlugs") == [slug] and old in (b.get("nodeNames") or []):
                    b["nodeNames"].append(new)
            self._send(200, {"ok": True})
            return True
        return False

    def do_POST(self):
        self._record()
        if self._hooks():
            return
        m = BY_NAME.match(self.path)
        if not m or not m.group("verb"):
            return self._error(404, "not_found", self.path)
        slug, name, verb = m.group("slug"), m.group("name"), m.group("verb")
        if not self._auth_by_name(slug, name):
            return
        i, key = _find(slug, name)
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
                if _is_ws(i["mode"]["slug"])
                else None
            )
            answer = {
                "machineRevision": i["machineRevision"],
                "secretKey": SECRET,
                "node": {"port": 2222, "name": i["name"], "mode": i["mode"]},
                "ingress": ingress,
                "origin": i["origin"],
            }
            if STATE["scenario"] != "old_fcp":
                answer["firewall"] = _firewall(i, key)
            return self._send(200, answer)
        if verb == "applied":
            body = self._body()
            try:
                reported = _firewall_report(body)
            except ValueError as e:
                return self._error(400, "validation", f"The applied report is not usable: {e}")
            if i["retirement"]:
                return self._error(409, "servers.node_retiring", "This node is being retired")
            rev = int(body.get("appliedRevision") or 0)
            if STATE["scenario"] == "stale_revision" or rev < i["machineRevision"]:
                return self._error(409, "servers.revision_stale", "Run the role again")
            if rev > i["machineRevision"]:
                return self._error(409, "servers.revision_unknown", "Never served")
            if body.get("nodeStarted") is not True:
                return self._error(400, "validation", "nodeStarted")
            STATE["applied_reports"].append({"name": i["name"], "firewall": reported})
            # The firewall report holds whatever the revision (a re-report too).
            for c in STATE["port_changes"]:
                if c["key"] == key and c["state"] == "waiting" and reported is not None and c["port"] in reported:
                    c["state"] = "confirmed"
                    STATE["confirmations"].append({"id": c["id"], "name": i["name"], "port": c["port"]})
            i["appliedRevision"] = rev
            if i["stage"] in ("registered", "bootstrap_available"):
                i["stage"] = "machine_applied"
            if i["stage"] == "machine_applied" and STATE["scenario"] != "not_ready":
                i["stage"] = "machine_ready"
            return self._send(200, _view(i, key))
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
