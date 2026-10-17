#!/usr/bin/env python3
"""A fake host firewall for the role's offline tests (tests/test_firewall.yml).

Stands in for `ufw`, `firewall-cmd`, `nft` and `systemctl` (the test writes one
wrapper per tool into a scratch PATH directory), with just the subcommands the
role's tasks/setup/firewall/*.yml run. State lives in the JSON file named by
$FAKE_FW_STATE, and every call is appended to its `calls`, so a test can say
what the role ran and what it left alone. It also reads the files the role
writes (the ufw profile, the firewalld service, the nftables set file) the way
the real tools would, and refuses one they would refuse.

State:
  active       units `systemctl is-active` answers active for
  ufw          {installed, enabled, rules: ["ufw allow 22/tcp", ...], dir}
               (`dir` holds applications.d/ and user.rules, as /etc/ufw does)
  firewalld    {running, default_zone, dir, known: {service: [ports]},
                permanent: {zone: {services, ports}}, runtime: {...}}
  nft          {tables: {"inet filter": {sets: {name: [ports]},
                chains: [{name, hook?, type?, rules: [text]}]}}}

Usage: fake_firewall.py <tool> [args...]
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

STATE_FILE = os.environ["FAKE_FW_STATE"]


def load():
    with open(STATE_FILE) as f:
        return json.load(f)


def save(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def out(text="", rc=0, err=""):
    if text:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    sys.exit(rc)


# --- systemctl -------------------------------------------------------------------


def systemctl(state, args):
    if len(args) == 2 and args[0] == "is-active":
        if args[1] in state.get("active", []):
            out("active")
        out("inactive", 3)
    out(err="fake systemctl: unsupported " + " ".join(args), rc=2)


# --- ufw -------------------------------------------------------------------------

UFW_PROFILE = re.compile(r"^ports=([0-9]+(?:,[0-9]+)*)/tcp$", re.M)


def ufw_profile_ports(u, name):
    """The ports of an application profile, as ufw parses it; None when ufw would refuse it."""
    path = os.path.join(u["dir"], "applications.d", name)
    if not os.path.exists(path):
        return None
    text = open(path).read()
    if f"[{name}]" not in text or "title=" not in text or "description=" not in text:
        return None
    m = UFW_PROFILE.search(text)
    if not m:
        return None
    ports = [int(p) for p in m.group(1).split(",")]
    if len(ports) > 15 or any(not 1 <= p <= 65535 for p in ports):
        return None
    return ports


def ufw_write_user_rules(u):
    """user.rules: one tuple per rule, an application rule carrying its expanded ports."""
    lines = ["*filter", ":ufw-user-input - [0:0]", "### RULES ###"]
    for r in u["rules"]:
        m = re.match(r"^ufw allow ([0-9]+)/tcp$", r)
        if m:
            lines.append(f"### tuple ### allow tcp {m.group(1)} 0.0.0.0/0 any 0.0.0.0/0 in")
            continue
        m = re.match(r"^ufw allow ([A-Za-z0-9-]+)$", r)
        if m:
            ports = u["app_ports"].get(m.group(1), [])
            lines.append(
                f"### tuple ### allow tcp {','.join(str(p) for p in ports)} 0.0.0.0/0 any 0.0.0.0/0 {m.group(1)} - in"
            )
    lines += ["### END RULES ###", "COMMIT"]
    with open(os.path.join(u["dir"], "user.rules"), "w") as f:
        f.write("\n".join(lines) + "\n")


def ufw(state, args):
    u = state["ufw"]
    if not u.get("installed"):
        out(err="ufw: command not found", rc=127)
    u.setdefault("app_ports", {})
    if args == ["status"]:
        out("Status: active" if u.get("enabled") else "Status: inactive")
    if args == ["show", "added"]:
        out("Added user rules (see 'ufw status' for running firewall):\n" + "\n".join(u["rules"]))
    if len(args) == 2 and args[0] == "allow" and not re.match(r"^[0-9]", args[1]):
        ports = ufw_profile_ports(u, args[1])
        if ports is None:
            out(err=f"ERROR: Could not find a profile matching '{args[1]}'", rc=1)
        rule = f"ufw allow {args[1]}"
        if rule in u["rules"]:
            out("Skipping adding existing rule")
        u["rules"].append(rule)
        u["app_ports"][args[1]] = ports
        ufw_write_user_rules(u)
        save(state)
        out("Rule added\nRule added (v6)")
    if len(args) == 3 and args[:2] == ["delete", "allow"]:
        if ufw_profile_ports(u, args[2]) is None and not re.match(r"^[0-9]", args[2]):
            out(err=f"ERROR: Could not find a profile matching '{args[2]}'", rc=1)
        rule = f"ufw allow {args[2]}"
        if rule not in u["rules"]:
            out("Could not delete non-existent rule")
        u["rules"].remove(rule)
        u["app_ports"].pop(args[2], None)
        ufw_write_user_rules(u)
        save(state)
        out("Rule deleted\nRule deleted (v6)")
    if len(args) == 3 and args[:2] == ["app", "update"]:
        ports = ufw_profile_ports(u, args[2])
        if ports is None:
            out(err=f"ERROR: Could not find a profile matching '{args[2]}'", rc=1)
        if f"ufw allow {args[2]}" in u["rules"]:
            u["app_ports"][args[2]] = ports
            ufw_write_user_rules(u)
            save(state)
        out(f"Rules updated for profile '{args[2]}'")
    out(err="fake ufw: unsupported " + " ".join(args), rc=2)


# --- firewall-cmd ------------------------------------------------------------------


def firewalld_load_services(f):
    """What a reload reads from the services directory; refuses a definition firewalld would."""
    known = {}
    d = f["dir"]
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".xml"):
            continue
        root = ET.parse(os.path.join(d, fn)).getroot()  # raises on bad XML
        if root.tag != "service":
            raise ValueError(fn)
        ports = []
        for p in root.findall("port"):
            if p.get("protocol") not in ("tcp", "udp") or not p.get("port", "").isdigit():
                raise ValueError(fn)
            ports.append(f"{p.get('port')}/{p.get('protocol')}")
        known[fn[: -len(".xml")]] = ports
    return known


def firewall_cmd(state, args):
    f = state["firewalld"]
    if args == ["--state"]:
        if f.get("running"):
            out("running")
        out("not running", 252)
    if not f.get("running"):
        out(err="FirewallD is not running", rc=252)
    if args == ["--get-default-zone"]:
        out(f["default_zone"])
    if args == ["--reload"]:
        if f.get("fail_reload"):
            out(err="Error: COMMAND_FAILED", rc=1)
        try:
            f["known"] = {**f.get("builtin", {}), **firewalld_load_services(f)}
        except Exception as e:  # noqa: BLE001 - firewalld refuses the reload
            out(err=f"Error: INVALID_SERVICE: {e}", rc=101)
        f["runtime"] = json.loads(json.dumps(f["permanent"]))
        save(state)
        out("success")
    m = re.match(r"^--info-service=(.+)$", args[0]) if len(args) == 1 else None
    if m:
        ports = f.get("known", {}).get(m.group(1))
        if ports is None:
            out(err=f"Error: INVALID_SERVICE: {m.group(1)}", rc=101)
        out(f"{m.group(1)}\n  ports: {' '.join(ports)}\n  protocols: \n  source-ports: \n  modules: \n  destination: ")
    permanent = "--permanent" in args
    rest = [a for a in args if a != "--permanent"]
    where = f["permanent"] if permanent else f["runtime"]
    zone = f["default_zone"]
    op = None
    for a in rest:
        if a.startswith("--zone="):
            zone = a[len("--zone="):]
        elif re.match(r"^--(query|add|remove)-service=", a):
            op = a
    if op is None:
        out(err="fake firewall-cmd: unsupported " + " ".join(args), rc=2)
    verb, service = re.match(r"^--(query|add|remove)-service=(.+)$", op).groups()
    z = where.setdefault(zone, {"services": [], "ports": []})
    if verb == "query":
        if service in z["services"]:
            out("yes")
        out("no", 1)
    if service not in f.get("known", {}):
        out(err=f"Error: INVALID_SERVICE: {service}", rc=101)
    if verb == "add":
        if service not in z["services"]:
            z["services"].append(service)
    elif service in z["services"]:
        z["services"].remove(service)
    save(state)
    out("success")


# --- nft ---------------------------------------------------------------------------

SET_FILE = re.compile(
    r"^\s*set freesocks_fcp \{\s*type inet_service\s*(?:elements = \{ ([0-9]+(?:, [0-9]+)*) \}\s*)?\}\s*$"
)


def nft_check_set_file(path):
    """The role's set file, as nft would parse it inside a table; None when nft would refuse it."""
    text = "\n".join(line for line in open(path).read().splitlines() if not line.lstrip().startswith("#"))
    m = SET_FILE.match(text)
    if not m:
        return None
    return [int(p) for p in m.group(1).split(", ")] if m.group(1) else []


def nft_rule_expr(text):
    """A rule as `nft -j` lists it, for the few forms the tests write."""
    m = re.match(r"^tcp dport (\S+)( counter)? (accept|drop)$", text)
    if not m:
        return [{"xt": {"text": text}}]
    right = m.group(1) if m.group(1).startswith("@") else int(m.group(1))
    expr = [{"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": right}}]
    if m.group(2):
        expr.append({"counter": {"packets": 0, "bytes": 0}})
    expr.append({m.group(3): None})
    return expr


def nft_list_table_json(table, t):
    fam, name = table.split(" ")
    items = [{"metainfo": {"version": "1.1.3", "json_schema_version": 1}}, {"table": {"family": fam, "name": name, "handle": 1}}]
    for c in t.get("chains", []):
        chain = {"family": fam, "table": name, "name": c["name"], "handle": 1}
        if c.get("hook"):
            chain.update({"type": c.get("type", "filter"), "hook": c["hook"], "prio": 0, "policy": "accept"})
        items.append({"chain": chain})
    for sname, elems in t.get("sets", {}).items():
        s = {"family": fam, "name": sname, "table": name, "type": "inet_service", "handle": 3}
        if elems:
            s["elem"] = sorted(elems)
        items.append({"set": s})
    for c in t.get("chains", []):
        for r in c.get("rules", []):
            items.append({"rule": {"family": fam, "table": name, "chain": c["name"], "handle": 4, "expr": nft_rule_expr(r)}})
    return {"nftables": items}


def nft(state, args):
    n = state["nft"]
    stdin = sys.stdin.read() if args[-2:] == ["-f", "/dev/stdin"] else ""
    if args == ["-c", "-f", "/dev/stdin"]:
        m = re.search(r'include "([^"]+)"', stdin)
        if not m or not re.match(r"^table inet freesocks_fcp_check \{", stdin.strip()):
            out(err="Error: syntax error", rc=1)
        if nft_check_set_file(m.group(1)) is None:
            out(err="Error: syntax error in the included set", rc=1)
        out()
    if args == ["-f", "/dev/stdin"]:
        tables = json.loads(json.dumps(n["tables"]))  # one transaction: all or nothing
        for line in [l.strip() for l in stdin.splitlines() if l.strip()]:
            m = re.match(r"^flush set (\S+ \S+) (\S+)$", line)
            if m and m.group(2) in tables.get(m.group(1), {}).get("sets", {}):
                tables[m.group(1)]["sets"][m.group(2)] = []
                continue
            m = re.match(r"^add element (\S+ \S+) (\S+) \{ ([0-9]+(?:, [0-9]+)*) \}$", line)
            if m and m.group(2) in tables.get(m.group(1), {}).get("sets", {}):
                s = tables[m.group(1)]["sets"][m.group(2)]
                s.extend(int(p) for p in m.group(3).split(", ") if int(p) not in s)
                continue
            out(err=f"Error: could not process rule: {line}", rc=1)
        n["tables"] = tables
        state.setdefault("nft_transactions", []).append(stdin)
        save(state)
        out()
    if len(args) == 6 and args[:3] == ["-j", "list", "set"]:
        table, name = f"{args[3]} {args[4]}", args[5]
        s = n["tables"].get(table, {}).get("sets", {}).get(name)
        if s is None:
            out(err="Error: No such file or directory", rc=1)
        fam, tname = table.split(" ")
        body = {"family": fam, "name": name, "table": tname, "type": "inet_service", "handle": 4}
        if s:
            body["elem"] = sorted(s)
        out(json.dumps({"nftables": [{"metainfo": {"version": "1.0.9", "json_schema_version": 1}}, {"set": body}]}))
    if len(args) == 5 and args[:3] == ["-j", "list", "table"]:
        table = f"{args[3]} {args[4]}"
        t = n["tables"].get(table)
        if t is None:
            out(err="Error: No such file or directory", rc=1)
        out(json.dumps(nft_list_table_json(table, t)))
    out(err="fake nft: unsupported " + " ".join(args), rc=2)


def main():
    tool, args = sys.argv[1], sys.argv[2:]
    state = load()
    state.setdefault("calls", []).append([tool] + args)
    save(state)
    {"systemctl": systemctl, "ufw": ufw, "firewall-cmd": firewall_cmd, "nft": nft}[tool](state, args)


if __name__ == "__main__":
    main()
