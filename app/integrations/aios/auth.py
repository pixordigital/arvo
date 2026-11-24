"""Service-to-service auth para o peer AIOS. HMAC stdlib, sem novas deps.

Sign: assina requests outbound. Verify: valida inbound.
Chave = AIOS_SERVICE_KEY (peer), identificada por AIOS_SERVICE_KEY_ID.
"""

import hashlib
import hmac
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlencode

_PATH = "/api/integrations"
_CLOCK_SKEW = 300  # segundos de tolerância de timestamp

# Cache-only dedup; IntegrationNonce DB is authority and rejects cross-replica replay.
_seen_nonces: dict[str, float] = {}
_lock = threading.Lock()


def _register_nonce(nonce: str) -> bool:
    now = time.time()
    with _lock:
        for k, exp in list(_seen_nonces.items()):
            if exp < now:
                del _seen_nonces[k]
        if nonce in _seen_nonces:
            return False
        _seen_nonces[nonce] = now + _CLOCK_SKEW * 2
        return True


def _clear_nonces() -> None:
    with _lock:
        _seen_nonces.clear()


def _forget_nonce(nonce: str) -> None:
    with _lock:
        _seen_nonces.pop(nonce, None)


def _body_hash(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def _normalize_query(query: str | None) -> str:
    """Canonical query: sorted pairs, blank values kept; empty -> ''."""
    if not query:
        return ""
    return urlencode(sorted(parse_qsl(query, keep_blank_values=True)))


def _canonical(
    key_id: str,
    ts: str,
    nonce: str,
    method: str,
    path: str,
    bhash: str,
    query: str = "",
    idempotency_key: str = "",
) -> str:
    return f"{key_id}.{ts}.{nonce}.{method}.{path}.{query}.{idempotency_key}.{bhash}"


def sign_request(
    method: str,
    path: str,
    body: bytes | None,
    key_id: str,
    key: str,
    query: str = "",
    idempotency_key: str = "",
) -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    sig = hmac.new(
        key.encode(),
        _canonical(
            key_id, ts, nonce, method, path, _body_hash(body),
            _normalize_query(query), idempotency_key or "",
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Service-Key-Id": key_id,
        "X-Service-Timestamp": ts,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": sig,
    }


def verification_keys(primary_kid: str, primary_key: str, key_map: str, kid: str) -> list[str]:
    """Secrets aceitas para `kid`: mapa CSV `kid:secret,...` primeiro, primária depois."""
    candidates: list[str] = []
    for entry in (key_map or "").split(","):
        mapped_kid, sep, secret = entry.strip().partition(":")
        mapped_kid, secret = mapped_kid.strip(), secret.strip()
        if sep and mapped_kid == kid and secret:
            candidates.append(secret)
    if kid == primary_kid and primary_key and primary_key not in candidates:
        candidates.append(primary_key)
    return candidates


def verify_request(
    key_id: str,
    ts: str,
    nonce: str,
    method: str,
    path: str,
    body: bytes | None,
    signature: str,
    key: str,
    query: str = "",
    idempotency_key: str = "",
) -> bool:
    if not all([key_id, ts, nonce, signature, key]):
        return False
    try:
        if abs(int(time.time()) - int(ts)) > _CLOCK_SKEW:
            return False
    except ValueError:
        return False

    expected = hmac.new(
        key.encode(),
        _canonical(
            key_id, ts, nonce, method, path, _body_hash(body),
            _normalize_query(query), idempotency_key or "",
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    if not _register_nonce(nonce):
        return False
    return True


if __name__ == "__main__":
    kid, key = "test-kid", "test-secret"
    _clear_nonces()
    hdr = sign_request("POST", _PATH, b"{}", kid, key)
    assert verify_request(
        kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "POST", _PATH, b"{}", hdr["X-Service-Signature"], key
    )
    assert not verify_request(
        kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "POST", _PATH, b"{}", hdr["X-Service-Signature"], key
    ), "replay mesma nonce deve falhar"
    _clear_nonces()
    assert not verify_request(
        kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "POST", _PATH, b"{}", hdr["X-Service-Signature"], "wrong-key"
    )
    assert not verify_request(
        kid, str(int(time.time()) - 9999), "n2", "POST", _PATH, b"{}", hdr["X-Service-Signature"], key
    )
    print("auth self-check OK")
