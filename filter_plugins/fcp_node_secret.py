"""Filters for keeping the node's panel secret across runs (tasks/setup/remnanode_compose.yml).

The panel's node secret (SECRET_KEY) is base64 of a JSON object with four PEMs:
  nodeCertPem, nodeKeyPem  a node certificate and its key, minted afresh on
                           every GET /api/keygen, signed by the panel's CA,
                           valid for three years;
  caCertPem                the panel's CA (long-lived, one per panel);
  jwtPublicKey             the panel's JWT verification key (one per panel).
The panel keeps no record of the node certificates it mints, so a secret
served earlier stays valid for as long as its own certificate does and the
panel keeps the same CA and JWT key. FCP serves a new one on every bootstrap;
writing it out would recreate the node container (and drop every session)
on every run. These filters decide when the one already on the node can stay.

  fcp_node_secret_on_host(compose_text)
      The SECRET_KEY value in a rendered remnanode compose file, or "" when
      there is none.
  fcp_node_secret_choice(served, on_host, rotate=False, min_days=30)
      Why the node gets the secret it gets, as a code (never the secret):
        kept            the one on the node stays: same panel, its certificate
                        valid for at least min_days more and matching its key;
        rotation_asked  rotate was set;
        none_on_host    the node has no secret yet;
        served_unreadable  FCP's secret is not a node secret; it is written
                        as before (the node will refuse it, loudly);
        unreadable      the node's secret cannot be read or checked;
        other_panel     the node's secret is for another CA or JWT key (the
                        panel was re-keyed, or the node moved);
        expiring        its certificate ends within min_days, or has ended;
        cannot_check    no `cryptography` on the controller to read it.
      Only `kept` keeps the node's secret; every other code takes FCP's.

Neither filter puts a secret into an error or a return value other than the
compose value it was given.
"""

import base64
import binascii
import json
import re
from datetime import datetime, timedelta, timezone

FIELDS = ("nodeCertPem", "nodeKeyPem", "caCertPem", "jwtPublicKey")
_SECRET_LINE = re.compile(r"^\s*-\s*SECRET_KEY=(\S+)\s*$", re.MULTILINE)


def fcp_node_secret_on_host(compose_text):
    if not compose_text:
        return ""
    found = _SECRET_LINE.findall(str(compose_text))
    return found[0] if len(found) == 1 else ""


def _payload(secret):
    """The four PEMs as the node normalises them, or None."""
    if not secret or not isinstance(secret, str):
        return None
    try:
        parsed = json.loads(base64.b64decode(secret.strip(), validate=True).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    out = {}
    for field in FIELDS:
        value = parsed.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
        out[field] = _normalise_pem(value)
    return out


def _normalise_pem(pem):
    # The node's own normalizePem: literal \n, CRLF, blank lines.
    text = pem.replace("\\n", "\n").replace("\r\n", "\n")
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _not_after(cert):
    value = getattr(cert, "not_valid_after_utc", None)
    if value is None:  # cryptography < 42
        value = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return value


def _node_cert_good_for(payload, min_days, now):
    """'kept', 'expiring', 'unreadable' or 'cannot_check' for the node's own certificate."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return "cannot_check"
    try:
        node = x509.load_pem_x509_certificate(payload["nodeCertPem"].encode())
        ca = x509.load_pem_x509_certificate(payload["caCertPem"].encode())
        key = serialization.load_pem_private_key(payload["nodeKeyPem"].encode(), password=None)
        serialization.load_pem_public_key(payload["jwtPublicKey"].encode())
        if node.issuer != ca.subject:
            return "unreadable"
        verify = getattr(node, "verify_directly_issued_by", None)
        if verify is not None:  # cryptography >= 40
            verify(ca)
        spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(*spki) != node.public_key().public_bytes(*spki):
            return "unreadable"
    except Exception:  # any parse or signature failure; never echo the PEMs
        return "unreadable"
    if _not_after(node) - now < timedelta(days=min_days):
        return "expiring"
    return "kept"


def fcp_node_secret_choice(served, on_host, rotate=False, min_days=30, now=None):
    if rotate:
        return "rotation_asked"
    if not on_host:
        return "none_on_host"
    fresh = _payload(served)
    if fresh is None:
        return "served_unreadable"
    current = _payload(on_host)
    if current is None:
        return "unreadable"
    if fresh["caCertPem"] != current["caCertPem"] or fresh["jwtPublicKey"] != current["jwtPublicKey"]:
        return "other_panel"
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now = datetime.fromisoformat(now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    return _node_cert_good_for(current, int(min_days), now)


class FilterModule(object):
    def filters(self):
        return {
            "fcp_node_secret_on_host": fcp_node_secret_on_host,
            "fcp_node_secret_choice": fcp_node_secret_choice,
        }
