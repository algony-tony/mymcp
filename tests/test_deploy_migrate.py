def test_rewrite_env_keys_replaces_mcp_with_mymcp():
    from mymcp.deploy.migrate import rewrite_env_keys

    src = (
        "MCP_HOST=0.0.0.0\n"
        "MCP_PORT=8765\n"
        "MCP_ADMIN_TOKEN=adm\n"
        "MCP_AUDIT_ENABLED=true\n"
        "MCP_APP_DIR=/opt/mymcp\n"
    )
    out = rewrite_env_keys(src)
    assert "MYMCP_HOST=0.0.0.0" in out
    assert "MYMCP_PORT=8765" in out
    assert "MYMCP_ADMIN_TOKEN=adm" in out
    assert "MYMCP_AUDIT_ENABLED=true" in out
    # APP_DIR is dropped entirely
    assert "APP_DIR" not in out


def test_rewrite_env_keys_preserves_unknown_lines():
    from mymcp.deploy.migrate import rewrite_env_keys

    src = "# comment\n\nMCP_HOST=1.2.3.4\nFOO=bar\n"
    out = rewrite_env_keys(src)
    assert "# comment" in out
    assert "FOO=bar" in out
    assert "MYMCP_HOST=1.2.3.4" in out


def test_legacy_dir_present(tmp_path):
    from mymcp.deploy.migrate import legacy_dir_present

    assert legacy_dir_present(tmp_path) is False
    (tmp_path / ".env").write_text("MCP_HOST=0.0.0.0\n")
    assert legacy_dir_present(tmp_path) is True
