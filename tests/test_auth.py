import pytest
from pathlib import Path
from auth import TokenStore


def make_store(tmp_path: Path) -> TokenStore:
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


def test_create_token_has_tok_prefix(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-a")
    assert token.startswith("tok_")


def test_validate_returns_info_for_valid_token(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-a")
    info = store.validate(token)
    assert info is not None
    assert info["name"] == "client-a"
    assert info["enabled"] is True


def test_validate_returns_none_for_unknown_token(tmp_path):
    store = make_store(tmp_path)
    assert store.validate("tok_doesnotexist") is None


def test_revoke_removes_token(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-b")
    assert store.revoke_token(token) is True
    assert store.validate(token) is None


def test_revoke_returns_false_for_unknown_token(tmp_path):
    store = make_store(tmp_path)
    assert store.revoke_token("tok_unknown") is False


def test_admin_token_not_valid_as_user_token(tmp_path):
    store = make_store(tmp_path)
    assert store.validate("adm_testadmin") is None


def test_tokens_persist_across_instances(tmp_path):
    path = str(tmp_path / "tokens.json")
    store1 = TokenStore(path, "adm_testadmin")
    token = store1.create_token("client-c")

    store2 = TokenStore(path, "adm_testadmin")
    assert store2.validate(token) is not None


def test_list_tokens_returns_all(tmp_path):
    store = make_store(tmp_path)
    t1 = store.create_token("client-1")
    t2 = store.create_token("client-2")
    all_tokens = store.list_tokens()
    assert t1 in all_tokens
    assert t2 in all_tokens


def test_validate_updates_last_used(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-d")
    assert store.validate(token)["last_used"] is not None


def test_create_token_default_role_is_ro(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-ro")
    info = store.validate(token)
    assert info["role"] == "ro"


def test_create_token_with_rw_role(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-rw", role="rw")
    info = store.validate(token)
    assert info["role"] == "rw"


def test_backward_compat_missing_role_defaults_rw(tmp_path):
    """Tokens without a role field (from older versions) default to rw."""
    import json
    path = tmp_path / "tokens.json"
    old_data = {
        "tokens": {
            "tok_legacy": {
                "name": "old-client",
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_used": None,
                "enabled": True,
            }
        },
        "admin_token": "adm_testadmin",
    }
    path.write_text(json.dumps(old_data))
    store = TokenStore(str(path), "adm_testadmin")
    info = store.validate("tok_legacy")
    assert info is not None
    assert info["role"] == "rw"


def test_create_token_invalid_role_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.create_token("bad", role="admin")
