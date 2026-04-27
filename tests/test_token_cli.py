"""Tests for `mymcp token list/add/revoke/rotate-admin/rotate-metrics/disable-metrics`."""
import json
import os


def _run(*args, env_file: str | None = None):
    from mymcp.cli import main
    if env_file:
        os.environ["MYMCP_ENV_FILE"] = env_file
    return main(list(args))


def _bootstrap(tmp_path):
    """Write a minimal .env and tokens.json for token CLI tests."""
    env = tmp_path / ".env"
    tok = tmp_path / "tokens.json"
    env.write_text(
        "MYMCP_ADMIN_TOKEN=adm_initial\n"
        "MYMCP_METRICS_TOKEN=met_initial\n"
        f"MYMCP_TOKEN_FILE={tok}\n"
    )
    tok.write_text(json.dumps({"tokens": {}, "admin_token": "adm_initial"}))
    return env, tok


def test_token_add_creates_rw_token(tmp_path, capsys):
    env, tok = _bootstrap(tmp_path)
    rc = _run("token", "add", "--name", "laptop", "--role", "rw",
              env_file=str(env))
    assert rc == 0
    out = capsys.readouterr().out
    assert "tok_" in out
    body = json.loads(tok.read_text())
    names = [v["name"] for v in body["tokens"].values()]
    assert "laptop" in names


def test_token_list_shows_admin_metrics_status(tmp_path, capsys):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "list", env_file=str(env))
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "admin" in out
    assert "metrics" in out


def test_token_revoke_removes(tmp_path):
    env, tok = _bootstrap(tmp_path)
    body = json.loads(tok.read_text())
    body["tokens"]["tok_xxx"] = {
        "name": "old", "role": "ro", "enabled": True,
        "created_at": "x", "last_used": None,
    }
    tok.write_text(json.dumps(body))

    rc = _run("token", "revoke", "tok_xxx", env_file=str(env))
    assert rc == 0
    body = json.loads(tok.read_text())
    assert "tok_xxx" not in body["tokens"]


def test_token_rotate_admin_updates_env(tmp_path, capsys):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "rotate-admin", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_ADMIN_TOKEN=adm_initial" not in text
    out = capsys.readouterr().out
    assert "tok_" in out


def test_token_rotate_metrics_updates_env(tmp_path):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "rotate-metrics", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_METRICS_TOKEN=met_initial" not in text
    assert "MYMCP_METRICS_TOKEN=tok_" in text


def test_token_disable_metrics_blanks_env(tmp_path):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "disable-metrics", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_METRICS_TOKEN=met_initial" not in text
    # The line should still be present but empty
    assert any(ln.startswith("MYMCP_METRICS_TOKEN=") for ln in text.splitlines())
