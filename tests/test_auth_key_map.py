"""verification_keys CSV parsing: whitespace, malformed entries, primary fallback."""
from app.integrations.aios.auth import _clear_nonces, sign_request, verification_keys, verify_request

PATH = "/api/integrations/aios/v1/health"


def test_key_map_strips_entries_and_skips_malformed():
    primary_kid, primary_key = "kid-primary", "secret-primary"
    malformed = " kid-bad , :secret-no-kid, kid-no-secret:, kid-blank:   "
    assert verification_keys(primary_kid, primary_key, " kid-spaced : secret-spaced ", "kid-spaced") == ["secret-spaced"]
    assert verification_keys(primary_kid, primary_key, malformed, "kid-bad") == []
    assert verification_keys(primary_kid, primary_key, malformed, "kid-primary") == [primary_key]
    assert verification_keys(primary_kid, primary_key, " kid-primary : secret-rotated ", "kid-primary") == ["secret-rotated", primary_key]


def test_key_map_spaced_entry_verifies_and_tamper_rejected():
    _clear_nonces()
    try:
        hdr = sign_request("GET", PATH, None, "kid-spaced", "secret-spaced")
        keys = verification_keys("kid-primary", "secret-primary", " kid-spaced : secret-spaced ", "kid-spaced")
        assert keys == ["secret-spaced"]
        common = (
            "kid-spaced", hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"],
            "GET", PATH, None, hdr["X-Service-Signature"],
        )
        assert not verify_request(*common, keys[0], query="tampered=1")
        assert verify_request(*common, keys[0])
    finally:
        _clear_nonces()
