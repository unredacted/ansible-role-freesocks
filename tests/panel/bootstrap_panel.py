#!/usr/bin/env python3
"""Bootstrap the ephemeral Remnawave test panel (tests/panel/docker-compose.yml)
and mint an admin API token for the integration playbook.

Flow: wait for the panel -> register the first superadmin (fresh DB) or log in
-> mint a full-scope API token. Prints two lines to STDOUT (everything else to
stderr) so a wrapper can capture them:

    REMNAWAVE_TEST_URL=http://127.0.0.1:3000
    REMNAWAVE_TEST_TOKEN=<token>

Usage:  eval "$(python3 tests/panel/bootstrap_panel.py)"
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("REMNAWAVE_TEST_URL", "http://127.0.0.1:3000")
# Remnawave requires a 24+ char password with upper + lower + digit.
USERNAME = os.environ.get("REMNAWAVE_TEST_USER", "roleadmin")
PASSWORD = os.environ.get("REMNAWAVE_TEST_PASS", "RoleIntegrationTestPw12345678")


def log(*a):
    print("[rw-bootstrap]", *a, file=sys.stderr)


def api(path, method="GET", token=None, body=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("content-type", "application/json")
    req.add_header("accept", "application/json")
    # Dashboard (admin-JWT) requests must identify as the browser client, or the
    # panel's JwtDefaultGuard rejects an ADMIN token ("create an API-token in
    # the dashboard"). API-token (ROLE.API) callers skip this check entirely.
    req.add_header("x-remnawave-client-type", "browser")
    if token:
        req.add_header("authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as res:
            text = res.read().decode()
            return res.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def wait_for_panel():
    deadline = time.time() + 180
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            status, body = api("/api/auth/status")
            if status == 200 and body and body.get("response"):
                log(f"panel ready after {attempt} attempt(s)")
                return body["response"]
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit(f"panel not ready at {BASE} after 180s")


def main():
    log("target", BASE)
    status = wait_for_panel()
    log(f"auth status: register={status.get('isRegisterAllowed')} login={status.get('isLoginAllowed')}")

    creds = {"username": USERNAME, "password": PASSWORD}
    if status.get("isRegisterAllowed"):
        code, body = api("/api/auth/register", "POST", body=creds)
        if code not in (200, 201):
            raise SystemExit(f"register failed: {code} {json.dumps(body)[:300]}")
        log("registered first superadmin")
    else:
        code, body = api("/api/auth/login", "POST", body=creds)
        if code != 200:
            raise SystemExit(f"login failed: {code} {json.dumps(body)[:300]}")
        log("logged in")
    access_token = body["response"]["accessToken"]

    # Mint a long-lived, full-scope API token (the Bearer the role uses). NB:
    # the api-tokens controller is mounted at /api/tokens (the 'api-tokens' in
    # the OpenAPI is only an RBAC resource label, not the route).
    code, body = api(
        "/api/tokens",
        "POST",
        token=access_token,
        body={"name": "role-integration", "expiresInDays": 3650, "scopes": ["*"]},
    )
    if code not in (200, 201) or not body.get("response", {}).get("token"):
        raise SystemExit(f"token mint failed: {code} {json.dumps(body)[:300]}")
    log("minted API token")

    print(f"REMNAWAVE_TEST_URL={BASE}")
    print(f"REMNAWAVE_TEST_TOKEN={body['response']['token']}")


if __name__ == "__main__":
    main()
