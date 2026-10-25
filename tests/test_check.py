from cardcoding import check, post
from cardcoding.mock_xero import FakeXero

from conftest import CARD


def test_check_paginates_and_splits_reconciled(approved, tmp_path, monkeypatch):
    monkeypatch.setattr(check, "PAGE_SIZE", 5)
    xero = FakeXero(page_size=5)
    log = tmp_path / "log.csv"
    post.run(approved, log, CARD, client=xero, do_post=True)
    xero.txns[0]["IsReconciled"] = True
    xero.txns[1]["IsReconciled"] = True
    xero.calls.clear()
    result = check.run(log, CARD, xero)
    assert result["posted"] == 24 and result["reconciled"] == 2 and len(result["waiting"]) == 22
    assert sum(1 for c in xero.calls if c == ("GET", "BankTransactions")) == 5  # 24 rows at 5 per page


def test_check_flags_voided(approved, tmp_path):
    xero = FakeXero()
    log = tmp_path / "log.csv"
    post.run(approved, log, CARD, client=xero, do_post=True)
    xero.txns[3]["Status"] = "DELETED"
    assert check.run(log, CARD, xero)["missing"] == ["4"]
