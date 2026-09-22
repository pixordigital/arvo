"""Fase 1 — AIOS integration config + auth self-check. Fase 2 — HMAC + nonce + routes."""
import os
import sys

from app.integrations.aios.auth import _clear_nonces, sign_request, verify_request

PATH = "/api/integrations/arvo/v1/health"
ROUTE = "/api/v1/integrations/aios/v1/health"


def test_config_feature_flag_off_default():
    from app.core.config import settings
    assert settings.aios_integration_enabled is False
    assert settings.aios_service_key_id == ""
    assert settings.aios_service_key == ""


def test_sign_verify_roundtrip():
    kid, key = "kid-1", "secret-1"
    hdr = sign_request("GET", PATH, None, kid, key)
    assert verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)


def test_verify_rejects_wrong_key():
    kid, key = "kid-1", "secret-1"
    hdr = sign_request("GET", PATH, None, kid, key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], "wrong")


def test_verify_rejects_stale_timestamp():
    kid, key = "kid-1", "secret-1"
    stale = str(int(sys.maxsize // (10**9) * 0.5))  # epoch antigo
    sig = sign_request("GET", PATH, None, kid, key)["X-Service-Signature"]
    assert not verify_request(kid, stale, "n", "GET", PATH, None, sig, key)


def test_verify_rejects_bad_body_tamper():
    kid, key = "kid-1", "secret-1"
    _clear_nonces()
    hdr = sign_request("POST", PATH, b'{"a":1}', kid, key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "POST", PATH, b'{"a":2}', hdr["X-Service-Signature"], key)


def test_verify_rejects_replay():
    kid, key = "kid-1", "secret-1"
    _clear_nonces()
    hdr = sign_request("GET", PATH, None, kid, key)
    assert verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)
    _clear_nonces()


def test_health_requires_auth_when_enabled():
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.main import app

    c = TestClient(app)
    orig_enabled, orig_id, orig_key = settings.aios_integration_enabled, settings.aios_service_key_id, settings.aios_service_key
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-1"
        settings.aios_service_key = "secret-1"
        _clear_nonces()
        assert c.get(ROUTE).status_code == 401
        hdr = sign_request("GET", ROUTE, None, "kid-1", "secret-1")
        assert c.get(ROUTE, headers=hdr).status_code == 200
        assert c.get(ROUTE, headers=hdr).status_code == 401  # replay
    finally:
        settings.aios_integration_enabled = orig_enabled
        settings.aios_service_key_id = orig_id
        settings.aios_service_key = orig_key
        _clear_nonces()


def test_events_idempotency():
    import json
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    c = TestClient(app)
    orig_enabled, orig_id, orig_key = settings.aios_integration_enabled, settings.aios_service_key_id, settings.aios_service_key
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-1"
        settings.aios_service_key = "secret-1"
        _clear_nonces()
        _clear_events()
        path = "/api/v1/integrations/aios/v1/events"
        body = json.dumps({"type": "test.event", "payload": {"a": 1}}, separators=(",", ":")).encode()
        hdr = sign_request("POST", path, body, "kid-1", "secret-1")
        hdr["Idempotency-Key"] = "idem-1"
        hdr["Content-Type"] = "application/json"
        r = c.post(path, content=body, headers=hdr)
        assert r.status_code == 200 and r.json()["deduplicated"] is False
        hdr2 = sign_request("POST", path, body, "kid-1", "secret-1")
        hdr2["Idempotency-Key"] = "idem-1"
        hdr2["Content-Type"] = "application/json"
        r2 = c.post(path, content=body, headers=hdr2)
        assert r2.json()["deduplicated"] is True
    finally:
        settings.aios_integration_enabled = orig_enabled
        settings.aios_service_key_id = orig_id
        settings.aios_service_key = orig_key
        _clear_nonces()
        _clear_events()
