import pytest

from accretion.ids import has_prefix, new_id
from accretion.redaction import redact


def test_ids_are_prefixed_unique_and_sortable() -> None:
    first = new_id("run")
    second = new_id("run")
    assert has_prefix(first, "run")
    assert first != second
    assert len(first) == 30


def test_recursive_redaction_removes_secret_values() -> None:
    value = {
        "authorization": "Bearer very-secret-value",
        "nested": {"api_key": "sk-abcdefghijklmnop", "safe": "visible"},
        "message": "Bearer another-secret-value",
    }
    redacted = redact(value)
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["api_key"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "visible"
    assert redacted["message"] == "Bearer [REDACTED]"


@pytest.mark.acceptance("V01-P4-002")
def test_auth_material_is_redacted() -> None:
    value = {
        "code_verifier": "pkce-verifier-value",
        "nonce": "login-nonce",
        "state": "login-state",
        "session_id": "aus_01ABCDEF",
        "log": "callback issued id_token eyJhbGciOi.eyJzdWIiOiJh.c2lnbmF0dXJl for user",
    }
    redacted = redact(value)
    assert redacted["code_verifier"] == "[REDACTED]"
    assert redacted["nonce"] == "[REDACTED]"
    assert redacted["state"] == "[REDACTED]"
    assert redacted["session_id"] == "[REDACTED]"
    assert "eyJ" not in redacted["log"]
    assert "[REDACTED]" in redacted["log"]
