import csv

from cardcoding.__main__ import main


def test_demo_runs_end_to_end_offline(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Refusing to post: 7 line(s)" in out
    assert "Created 24; errors: 0" in out
    assert "skipped: 24 already in log" in out
    assert "reconciled: 12" in out
    assert len(list(csv.DictReader(open(tmp_path / "posted_log.csv")))) == 24


def test_cli_post_dry_run_needs_no_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XERO_CLIENT_ID", raising=False)
    main(["learn", "--demo", "--out", str(tmp_path)])
    main(["propose", "--demo", "--out", str(tmp_path)])
    main(["post", "--demo", "--out", str(tmp_path)])
    assert "dry run - nothing created" in capsys.readouterr().out
    assert not (tmp_path / "posted_log.csv").exists()
