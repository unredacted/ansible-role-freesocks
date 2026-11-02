#!/usr/bin/env python3
"""A mock Docker Engine API for the role's offline tests (tests/test_image_pull.yml).

Implements only what tasks/setup/image_pull.yml calls through community.docker
(docker_image_info; docker_image pull, tag and absent), answering the way
dockerd does:

  GET    /_ping, /version                   the client's handshake
  GET    /images/json                       the image list (the module filters by tag)
  GET    /images/{name}/json                inspect (404 when absent)
  POST   /images/create?fromImage=&tag=     a pull, answered from the scenario
  POST   /images/{name}/tag?repo=&tag=      tag (the tag moves off any other image)
  DELETE /images/{name}                     untag (the image goes with its last tag)
  GET    /__state                           test hook: the images and every pull
  POST   /__scenario {...}                  test hook: start over with this scenario

Every pull is recorded: the reference, where it went (hub, mirror or other),
the user its X-Registry-Auth carried and whether its token was the right one
(the token itself is not kept), the lines of the hosts file that named
registry-1.docker.io or auth.docker.io at that moment, whether they pinned
registry-1.docker.io to IPv4, and what it answered.

Scenario:
  images         ["remnawave/node:3.4.2"]   tags there before the run
  hub            ["429", "429", "ok"]       one outcome per pull from Docker Hub,
  mirror         ["ok"]                     the mirror (a reference that starts
  other          ["ok"]                     with mirror_prefix) and any other
                                            registry; the last one repeats
  mirror_prefix  "mirror.example:5000"
  hub_token      "..."                      a Docker Hub pull that carries a login
                                            must carry this token (401 otherwise)

Outcomes:
  ok             the image is pulled (its progress streamed, chunked like dockerd)
  429            HTTP 429 toomanyrequests before any progress (dockerd's usual answer)
  429-stream     the rate limit as the error line of a progress stream
  429-unpinned   429 unless the hosts file pins registry-1.docker.io to IPv4, else ok
  unknown        HTTP 404 manifest unknown
  down           HTTP 500, as for a mirror that cannot be reached

Run:  python3 tests/mock_docker.py <port> <hosts file>
"""

import base64
import hashlib
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HOSTS_FILE = sys.argv[2] if len(sys.argv) > 2 else "/etc/hosts"
HUB_NAMES = ("docker.io", "index.docker.io", "registry-1.docker.io")
PINNED_NAMES = ("registry-1.docker.io", "auth.docker.io")
RATE_LIMIT = ("toomanyrequests: You have reached your unauthenticated pull rate limit. "
              "https://www.docker.com/increase-rate-limit")
LOCK = threading.Lock()
STATE = {}


def split_ref(ref):
    """(registry, path, tag) of an image reference, filled in as dockerd does."""
    tag = "latest"
    if ref.rfind(":") > ref.rfind("/"):
        ref, tag = ref.rsplit(":", 1)
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = "docker.io", ref
    if registry in HUB_NAMES:
        registry = "docker.io"
        if "/" not in path:
            path = "library/" + path
    return registry, path, tag


def normalize(ref):
    """The name dockerd lists an image under (RepoTags)."""
    registry, path, tag = split_ref(ref)
    if registry == "docker.io":
        name = path[len("library/"):] if path.startswith("library/") else path
    else:
        name = registry + "/" + path
    return "%s:%s" % (name, tag)


def image_id(ref):
    """The same image whether it came from Docker Hub or through the mirror."""
    prefix = STATE["scenario"]["mirror_prefix"] + "/"
    upstream = ref[len(prefix):] if ref.startswith(prefix) else ref
    return "sha256:" + hashlib.sha256(normalize(upstream).encode()).hexdigest()


def find(name):
    images = STATE["images"]
    if name.startswith("sha256:"):
        return images.get(name)
    tag = normalize(name)
    return next((i for i in images.values() if tag in i["RepoTags"]), None)


def add_tag(iid, tag):
    for i in STATE["images"].values():
        if tag in i["RepoTags"]:
            i["RepoTags"].remove(tag)
    STATE["images"].setdefault(iid, {"Id": iid, "RepoTags": []})["RepoTags"].append(tag)
    STATE["images"] = {k: v for k, v in STATE["images"].items() if v["RepoTags"] or k == iid}


def reset(scenario):
    STATE.clear()
    STATE["scenario"] = {
        "hub": ["ok"], "mirror": ["ok"], "other": ["ok"],
        "mirror_prefix": "mirror.example:5000", "hub_token": None,
    }
    STATE["scenario"].update(scenario)
    STATE.update({"images": {}, "pulls": [], "counts": {"hub": 0, "mirror": 0, "other": 0}, "unknown": []})
    for ref in STATE["scenario"].get("images", []):
        add_tag(image_id(ref), normalize(ref))


def hosts_lines():
    try:
        with open(HOSTS_FILE) as f:
            lines = [ln.split("#", 1)[0].split() for ln in f]
    except OSError:
        return []
    return [" ".join(ln) for ln in lines if len(ln) > 1 and set(ln[1:]) & set(PINNED_NAMES)]


def registry_auth(header):
    if not header:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    except ValueError:
        return {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body=None):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if body is not None else "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, lines):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for line in lines:
            chunk = (json.dumps(line) + "\r\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        self.wfile.write(b"0\r\n\r\n")

    def _request(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        u = urlparse(self.path)
        path = re.sub(r"^/v[0-9.]+(?=/)", "", unquote(u.path))
        return path, {k: v[0] for k, v in parse_qs(u.query).items()}, body

    def _unknown(self, path):
        STATE["unknown"].append("%s %s" % (self.command, path))
        self._send(404, {"message": "mock_docker: not implemented: %s %s" % (self.command, path)})

    def do_HEAD(self):
        self._request()
        self._send(200, b"OK")

    def do_GET(self):
        path, _, _ = self._request()
        with LOCK:
            if path == "/_ping":
                return self._send(200, b"OK")
            if path == "/version":
                return self._send(200, {"ApiVersion": "1.45", "MinAPIVersion": "1.24", "Version": "27.5.1",
                                        "Os": "linux", "Arch": "amd64", "KernelVersion": "6.1.0"})
            if path == "/__state":
                return self._send(200, STATE)
            if path == "/images/json":
                return self._send(200, [dict(i, RepoDigests=[], Created=0, Size=0) for i in STATE["images"].values()])
            m = re.match(r"^/images/(.+)/json$", path)
            if m:
                image = find(m.group(1))
                if not image:
                    return self._send(404, {"message": "No such image: %s" % m.group(1)})
                return self._send(200, dict(image, RepoDigests=[], Config={}, Created="2026-01-01T00:00:00Z"))
            self._unknown(path)

    def do_DELETE(self):
        path, _, _ = self._request()
        with LOCK:
            m = re.match(r"^/images/(.+)$", path)
            if not m:
                return self._unknown(path)
            image = find(m.group(1))
            if not image:
                return self._send(404, {"message": "No such image: %s" % m.group(1)})
            tag = normalize(m.group(1))
            image["RepoTags"].remove(tag)
            answer = [{"Untagged": tag}]
            if not image["RepoTags"]:
                del STATE["images"][image["Id"]]
                answer.append({"Deleted": image["Id"]})
            self._send(200, answer)

    def do_POST(self):
        path, query, body = self._request()
        with LOCK:
            if path == "/__scenario":
                reset(json.loads(body or b"{}"))
                return self._send(200, STATE["scenario"])
            if path == "/images/create":
                return self._pull(query)
            m = re.match(r"^/images/(.+)/tag$", path)
            if m:
                image = find(m.group(1))
                if not image:
                    return self._send(404, {"message": "No such image: %s" % m.group(1)})
                add_tag(image["Id"], normalize("%s:%s" % (query["repo"], query.get("tag") or "latest")))
                return self._send(201)
            self._unknown(path)

    def _pull(self, query):
        ref = "%s:%s" % (query["fromImage"], query.get("tag") or "latest")
        registry, path, tag = split_ref(ref)
        scenario = STATE["scenario"]
        if ref.startswith(scenario["mirror_prefix"] + "/"):
            kind = "mirror"
        elif registry == "docker.io":
            kind = "hub"
        else:
            kind = "other"
        outcomes = scenario[kind]
        outcome = outcomes[min(STATE["counts"][kind], len(outcomes) - 1)]
        STATE["counts"][kind] += 1
        auth = registry_auth(self.headers.get("X-Registry-Auth"))
        named = hosts_lines()
        pinned = any(re.match(r"^\d+\.\d+\.\d+\.\d+ .*\bregistry-1\.docker\.io\b", ln) for ln in named)
        if outcome == "429-unpinned":
            outcome = "ok" if pinned else "429"
        token_ok = None
        if kind == "hub" and auth is not None and scenario["hub_token"] is not None:
            token_ok = auth.get("password") == scenario["hub_token"]
            if not token_ok:
                outcome = "unauthorized"
        STATE["pulls"].append({
            "ref": normalize(ref), "kind": kind, "user": (auth or {}).get("username"),
            "token_ok": token_ok, "hosts": named, "pinned": pinned, "answer": outcome,
        })
        if outcome == "429":
            return self._send(429, {"message": RATE_LIMIT})
        if outcome == "429-stream":
            return self._stream([{"status": "Pulling from %s" % path, "id": tag},
                                 {"errorDetail": {"message": RATE_LIMIT}, "error": RATE_LIMIT}])
        if outcome == "unknown":
            return self._send(404, {"message": "manifest for %s not found: manifest unknown: manifest unknown" % ref})
        if outcome == "unauthorized":
            return self._send(401, {"message": "Head \"https://registry-1.docker.io/v2/%s/manifests/%s\": "
                                               "unauthorized: incorrect username or password" % (path, tag)})
        if outcome == "down":
            return self._send(500, {"message": "Get \"https://%s/v2/\": dial tcp: connect: connection refused"
                                               % scenario["mirror_prefix"]})
        iid = image_id(ref)
        add_tag(iid, normalize(ref))
        self._stream([{"status": "Pulling from %s" % path, "id": tag},
                      {"status": "Digest: %s" % iid},
                      {"status": "Status: Downloaded newer image for %s" % normalize(ref)}])


if __name__ == "__main__":
    reset({})
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8829
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
