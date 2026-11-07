"""Throwaway panel node secrets for tests/test_node_secret.yml.

Mints secrets shaped like the panel's GET /api/keygen pubKey: base64 of
{nodeCertPem, nodeKeyPem, caCertPem, jwtPublicKey}, the node certificate
signed by the panel's CA (ECDSA P-256, like the panel). Prints a JSON object:
  a1, a2, a3       panel A, three keygen calls (same CA and JWT key)
  a_expiring       panel A, a node certificate ending in 10 days
  a_foreign_cert   panel A's CA and JWT key, a node certificate from another CA
  b1               panel B (another CA and JWT key)

Usage: python3 tests/make_node_secret.py
"""

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID


def _pem_key(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _pem_cert(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def panel(cn):
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(cn))
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    jwt = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwt_pub = jwt.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return {"key": key, "cert": cert, "jwt": jwt_pub}


def secret(p, days=3 * 365, signer=None):
    signer = signer or p
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("node"))
        .issuer_name(signer["cert"].subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(signer["key"], hashes.SHA256())
    )
    payload = {
        "nodeCertPem": _pem_cert(cert),
        "nodeKeyPem": _pem_key(key),
        "caCertPem": _pem_cert(p["cert"]),
        "jwtPublicKey": p["jwt"],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


if __name__ == "__main__":
    a = panel("panel-a")
    b = panel("panel-b")
    # Same subject as A, another key: only the signature tells them apart.
    impostor = panel("panel-a")
    print(
        json.dumps(
            {
                "a1": secret(a),
                "a2": secret(a),
                "a3": secret(a),
                "a_expiring": secret(a, days=10),
                "a_foreign_cert": secret(a, signer=impostor),
                "b1": secret(b),
            }
        )
    )
