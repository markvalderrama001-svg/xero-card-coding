import csv

import pytest

from cardcoding import post, propose
from cardcoding.mock_xero import FakeXero

from conftest import CARD


class ExplodingClient:
    """Fails the test if any HTTP call is attempted."""

    def call(self, *a, **kw):
        raise AssertionError(f"unexpected API call: {a}")


def test_undecided_lines_block_posting(proposal, tmp_path):
    xero = FakeXero()
    with pytest.raises(post.ApprovalRequired):
        post.run(proposal, tmp_path / "log.csv", CARD, client=xero, do_post=True)
    assert xero.calls == []  # gate trips before any read or write
    assert not (tmp_path / "log.csv").exists()


def test_incomplete_decision_blocks_posting(approved, tmp_path):
    rows = propose.read_proposal(approved)
    for r in rows:
        if r["line"] == "16":
            r["contact"] = ""
    propose.write_proposal(rows, approved)
    assert post.gate(rows) == ["line 16: no contact"]
    with pytest.raises(post.ApprovalRequired):
        post.run(approved, tmp_path / "log.csv", CARD, client=FakeXero(), do_post=True)


def test_dry_run_makes_no_http_calls(approved, tmp_path):
    summary = post.run(approved, tmp_path / "log.csv", CARD, client=ExplodingClient(), do_post=False)
    assert summary["would_post"] == 24 and summary["posted"] == 0
    assert not (tmp_path / "log.csv").exists()


def test_dry_run_still_previews_when_undecided(proposal, tmp_path):
    summary = post.run(proposal, tmp_path / "log.csv", CARD, client=ExplodingClient(), do_post=False)
    assert summary["would_post"] == 18 and len(summary["blocked"]) == 7


def test_post_tags_references_and_logs_ids(approved, tmp_path):
    xero, log = FakeXero(), tmp_path / "log.csv"
    summary = post.run(approved, log, CARD, client=xero, do_post=True)
    assert summary["posted"] == 24
    refs = {t["Reference"] for t in xero.txns}
    assert refs == {f"CC rec {n}" for n in range(1, 26) if n != 20}  # line 20 was SKIP
    logged = list(csv.DictReader(open(log)))
    assert {r["BankTransactionID"] for r in logged} == {t["BankTransactionID"] for t in xero.txns}
    assert all(r["reference"] == f"CC rec {r['line']}" for r in logged)


def test_transaction_shape(approved):
    rows = {r["line"]: r for r in propose.read_proposal(approved)}
    spend = post.build_transaction(rows["1"], CARD)
    assert spend["Type"] == "SPEND" and spend["Contact"] == {"Name": "Acme Supplies"}
    assert spend["BankAccount"] == {"AccountID": CARD}
    assert spend["LineItems"][0]["UnitAmount"] == 49.0
    assert spend["LineItems"][0]["Tracking"] == [{"Name": "Brand", "Option": "Northwind Studio"}]
    assert post.build_transaction(rows["18"], CARD)["Type"] == "RECEIVE"
    assert "Tracking" not in post.build_transaction(rows["11"], CARD)["LineItems"][0]  # 3100 untracked


def test_rerun_is_idempotent_via_log(approved, tmp_path):
    xero, log = FakeXero(), tmp_path / "log.csv"
    post.run(approved, log, CARD, client=xero, do_post=True)
    second = post.run(approved, log, CARD, client=xero, do_post=True)
    assert second["posted"] == 0 and second["skipped_logged"] == 24
    assert len(xero.txns) == 24


def test_idempotent_even_if_log_is_lost(approved, tmp_path):
    xero = FakeXero()
    post.run(approved, tmp_path / "log1.csv", CARD, client=xero, do_post=True)
    again = post.run(approved, tmp_path / "log2.csv", CARD, client=xero, do_post=True)
    assert again["posted"] == 0 and again["skipped_xero"] == 24
    assert len(xero.txns) == 24


def test_rejected_lines_are_not_logged_so_they_retry(approved, tmp_path):
    rows = propose.read_proposal(approved)
    for r in rows:
        if r["line"] == "3":
            r["account"] = "9999"  # not in the chart of accounts
    propose.write_proposal(rows, approved)
    xero, log = FakeXero(), tmp_path / "log.csv"
    summary = post.run(approved, log, CARD, client=xero, do_post=True)
    assert summary["posted"] == 23 and summary["errors"] == 1
    assert "3" not in {r["line"] for r in csv.DictReader(open(log))}


def test_only_filter(approved, tmp_path):
    xero = FakeXero()
    summary = post.run(approved, tmp_path / "log.csv", CARD, client=xero, do_post=True, only={"1", "2"})
    assert summary["posted"] == 2
