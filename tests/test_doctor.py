def test_doctor_runs_and_returns_zero(monkeypatch, capsys):
    from mymcp.cli import main

    monkeypatch.setattr("mymcp.deploy.service.systemd_available", lambda: True)
    rc = main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "python" in out
