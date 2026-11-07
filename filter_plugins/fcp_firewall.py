"""Filters for the role's firewall step (tasks/setup/firewall/).

The step works on port KEYS: a string "<port>/<protocol>" ("443/tcp",
"443/udp"), so a port and a protocol travel together through every set
operation (union, difference, intersect) and 443/udp is never taken for
443/tcp. These filters turn what FCP lists, what the operator sets and what a
host firewall holds into keys, and keys back into what each needs.

  fcp_fw_keys(items, strict=False)
      Sorted, unique keys (by port, then protocol). An item is a key string, a
      bare port (int or digits: TCP), a comma-separated string of those, or a
      mapping {port, protocol?} (no protocol: TCP). An item whose protocol is
      neither tcp nor udp is dropped, or with strict=True refused; a port that
      is not a number in 1-65535 is always refused.
  fcp_fw_unknown(items)
      The items fcp_fw_keys drops for their protocol, as "<port>/<protocol>".
  fcp_fw_ports(keys, protocol=None)
      The ports of one protocol (all, when None), as sorted ints.
  fcp_fw_report(keys)
      [{port, protocol}], the applied report's `firewall.ports`.
  fcp_fw_ufw_profile(text)
      The keys of the role's ufw application profile (its `ports=` line).
  fcp_fw_ufw_rules(text)
      The keys of the role's ufw rule as user.rules holds it (one tuple per
      protocol).
  fcp_fw_firewalld_service(text)
      The keys of the role's firewalld service definition (its <port>s).
  fcp_fw_nft_file(text)
      The keys of the role's nftables set file (freesocks_fcp: TCP,
      freesocks_fcp_udp: UDP).
  fcp_fw_nft_set_keys(set)
      The keys of one live set as `nft -j list set` gives it ({name, elem}).
"""

import re
from collections.abc import Mapping

from ansible.errors import AnsibleFilterError

PROTOCOLS = ("tcp", "udp")
NFT_SETS = {"freesocks_fcp": "tcp", "freesocks_fcp_udp": "udp"}


def _port(value, what):
    if isinstance(value, bool):
        raise AnsibleFilterError(f"Not a port: {what!r}")
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise AnsibleFilterError(f"Not a port: {what!r}")
    if not 1 <= port <= 65535:
        raise AnsibleFilterError(f"A port outside 1-65535: {what!r}")
    return port


def _split(item):
    """(port, protocol) of one item, the protocol lower-cased and not yet checked."""
    if isinstance(item, Mapping):
        if "port" not in item:
            raise AnsibleFilterError(f"A port entry without a port: {item!r}")
        proto = item.get("protocol")
        proto = "tcp" if proto in (None, "") else str(proto).strip().lower()
        return _port(item["port"], item), proto
    text = str(item).strip()
    port, _, proto = text.partition("/")
    return _port(port, item), (proto.strip().lower() or "tcp")


def _items(value):
    if value is None:
        return []
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return [p for p in str(value).split(",") if p.strip()]
    if isinstance(value, Mapping):
        return [value]
    try:
        return list(value)
    except TypeError:
        raise AnsibleFilterError(f"Not a list of ports: {value!r}")


def _sort(pairs):
    return [f"{p}/{proto}" for p, proto in sorted(set(pairs))]


def fcp_fw_keys(value, strict=False):
    pairs = []
    for item in _items(value):
        port, proto = _split(item)
        if proto not in PROTOCOLS:
            if strict:
                raise AnsibleFilterError(f"Not tcp or udp: {item!r}")
            continue
        pairs.append((port, proto))
    return _sort(pairs)


def fcp_fw_unknown(value):
    out = []
    for item in _items(value):
        port, proto = _split(item)
        if proto not in PROTOCOLS:
            out.append(f"{port}/{proto}")
    return sorted(set(out))


def fcp_fw_ports(keys, protocol=None):
    out = set()
    for port, proto in (_split(k) for k in _items(keys)):
        if protocol is None or proto == protocol:
            out.add(port)
    return sorted(out)


def fcp_fw_report(keys):
    return [{"port": port, "protocol": proto} for port, proto in (_split(k) for k in fcp_fw_keys(keys, strict=True))]


def fcp_fw_ufw_profile(text):
    m = re.search(r"(?m)^ports=(\S+)$", text or "")
    if not m:
        return []
    pairs = []
    for entry in m.group(1).split("|"):
        ports, _, proto = entry.partition("/")
        if proto not in PROTOCOLS or not re.fullmatch(r"[0-9]+(,[0-9]+)*", ports):
            continue
        pairs += [(int(p), proto) for p in ports.split(",")]
    return _sort(pairs)


UFW_TUPLE = re.compile(r"(?m)^### tuple ### allow (tcp|udp) ([0-9,]+) \S+ \S+ \S+ freesocks-fcp .*$")


def fcp_fw_ufw_rules(text):
    pairs = []
    for proto, ports in UFW_TUPLE.findall(text or ""):
        pairs += [(int(p), proto) for p in ports.split(",") if p]
    return _sort(pairs)


FIREWALLD_PORT = re.compile(r'<port protocol="(tcp|udp)" port="([0-9]+)"\s*/>')


def fcp_fw_firewalld_service(text):
    return _sort((int(port), proto) for proto, port in FIREWALLD_PORT.findall(text or ""))


NFT_SET = re.compile(r"set (freesocks_fcp(?:_udp)?) \{\s*type inet_service\s*(?:elements = \{([0-9, ]*)\}\s*)?\}")


def fcp_fw_nft_file(text):
    body = "\n".join(line for line in (text or "").splitlines() if not line.lstrip().startswith("#"))
    pairs = []
    for name, elems in NFT_SET.findall(body):
        pairs += [(int(p), NFT_SETS[name]) for p in re.findall(r"[0-9]+", elems)]
    return _sort(pairs)


def fcp_fw_nft_set_keys(nft_set):
    proto = NFT_SETS.get((nft_set or {}).get("name"))
    if proto is None:
        return []
    ports = [e for e in (nft_set.get("elem") or []) if not isinstance(e, Mapping)]
    return _sort((_port(p, p), proto) for p in ports)


class FilterModule(object):
    def filters(self):
        return {
            "fcp_fw_keys": fcp_fw_keys,
            "fcp_fw_unknown": fcp_fw_unknown,
            "fcp_fw_ports": fcp_fw_ports,
            "fcp_fw_report": fcp_fw_report,
            "fcp_fw_ufw_profile": fcp_fw_ufw_profile,
            "fcp_fw_ufw_rules": fcp_fw_ufw_rules,
            "fcp_fw_firewalld_service": fcp_fw_firewalld_service,
            "fcp_fw_nft_file": fcp_fw_nft_file,
            "fcp_fw_nft_set_keys": fcp_fw_nft_set_keys,
        }
