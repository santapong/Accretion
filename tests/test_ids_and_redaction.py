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
