from cardcoding import learn, propose
from cardcoding.learn import payee_key


def row(payee, amount="-10.00", line="1"):
    return {"line": line, "date": "2026-10-01", "payee": payee, "amount": amount}


def test_payee_key_normalises_statement_text():
    assert payee_key("ACME SUPPLIES*SUB 0042 SEATTLE WA") == "ACME SUPPLIES"
    assert payee_key("SQ *BLUE BOTTLE CAFE 118") == "BLUE BOTTLE"
    assert payee_key("GLOBEX CLOUD 7731 SEATTLE WA") == "GLOBEX CLOUD"


def test_learn_takes_majority_coding_and_share(rules):
    acme = rules["ACME SUPPLIES"]
    assert (acme["account"], acme["tracking"], acme["contact"]) == ("6100", "Northwind Studio", "Acme Supplies")
    assert acme["account_share"] == 1.0 and acme["n"] == 12
    sky = rules["SKYLINE AIR"]
    assert sky["tracking"] == "Bluebird Goods" and sky["tracking_share"] == 0.6


def test_learn_ignores_deleted_history():
    hist = [{"Type": "SPEND", "Status": "DELETED", "Contact": {"Name": "Acme Supplies"},
             "LineItems": [{"Description": "ACME SUPPLIES", "AccountCode": "6100"}]}]
    assert learn.learn(hist, category="Brand") == {}


def test_known_payee_is_auto_coded(rules):
    out = propose.classify(row("GLOBEX CLOUD 9999 SEATTLE WA"), rules, threshold=0.8)
    assert out["needs_decision"] == "no"
    assert (out["account"], out["tracking"], out["contact"]) == ("6200", "Bluebird Goods", "Globex Cloud")
    assert float(out["confidence"]) == 1.0


def test_owner_draw_account_carries_no_tracking(rules):
    out = propose.classify(row("STREAMFLIX.COM"), rules, threshold=0.8)
    assert out["account"] == "3100" and out["tracking"] == "" and out["needs_decision"] == "no"


def test_new_payee_needs_decision(rules):
    out = propose.classify(row("STARK HARDWARE 4410"), rules, threshold=0.8)
    assert out["needs_decision"] == "yes" and out["account"] == ""
    assert "new payee" in out["reason"]


def test_split_history_needs_decision_with_best_guess(rules):
    out = propose.classify(row("UMBRELLA MARKET #221"), rules, threshold=0.8)
    assert out["needs_decision"] == "yes"
    assert out["account"] == "6600"  # best guess pre-filled for the reviewer
    assert "6600 x5 / 3100 x4" in out["reason"]


def test_tracking_split_needs_decision(rules):
    out = propose.classify(row("SKYLINE AIR 0062281"), rules, threshold=0.8)
    assert out["needs_decision"] == "yes" and "Bluebird Goods x3 / Summit Media x2" in out["reason"]


def test_thin_history_needs_decision(rules):
    out = propose.classify(row("MOONBEAM HOSTING 5521"), rules, threshold=0.8)
    assert out["needs_decision"] == "yes" and "only 2 prior" in out["reason"]


def test_credit_on_spend_payee_needs_decision(rules):
    out = propose.classify(row("GLOBEX CLOUD REFUND 7731", amount="212.40"), rules, threshold=0.8)
    assert out["needs_decision"] == "yes" and "refund" in out["reason"]


def test_cash_back_receive_is_auto_coded(rules):
    out = propose.classify(row("CASHBACK REWARD", amount="25.40"), rules, threshold=0.8)
    assert out["needs_decision"] == "no" and out["account"] == "4900"


def test_fixture_statement_split(proposal):
    rows = propose.read_proposal(proposal)
    assert len(rows) == 25
    undecided = sorted(int(r["line"]) for r in rows if r["needs_decision"] == "yes")
    assert undecided == [9, 10, 15, 16, 17, 18, 20]
