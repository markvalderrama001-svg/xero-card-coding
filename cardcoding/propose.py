"""Turn a card statement into a coding proposal a human reviews before anything posts.

Statement CSV columns: line, date (YYYY-MM-DD), payee, amount (negative = charge,
positive = credit). Proposal CSV adds: account, tracking, contact, confidence,
needs_decision (yes/no) and reason.

Review workflow: open the proposal, fix any line marked needs_decision=yes
(set account / tracking / contact), then change it to ``no``. Set account to
``SKIP`` for lines that must not post (transfers already recorded, a charge
and its same-day refund you'd rather net, etc.).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import config
from .learn import payee_key

PROPOSAL_FIELDS = ["line", "date", "payee", "amount", "account", "tracking", "contact",
                   "confidence", "needs_decision", "reason"]
SKIP = "SKIP"


def load_statement(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return [{"line": r["line"].strip(), "date": r["date"].strip(), "payee": r["payee"].strip(),
                 "amount": r["amount"].strip()} for r in csv.DictReader(f)]


def _split(hist: dict) -> str:
    return " / ".join(f"{k} x{v}" for k, v in sorted(hist.items(), key=lambda kv: -kv[1]))


def classify(row: dict, rules: dict, threshold: float | None = None) -> dict:
    """Propose a coding for one statement line. Never guesses silently: anything
    short of a clear historical pattern is flagged for a human."""
    threshold = config.confidence_threshold() if threshold is None else threshold
    out = dict(row, account="", tracking="", contact="", confidence="0.00", needs_decision="yes", reason="")
    rule = rules.get(payee_key(row["payee"]))
    if rule is None:
        out["reason"] = "new payee - no coded history"
        return out

    support = min(1.0, rule["n"] / 3)  # one or two sightings is not a pattern yet
    confidence = rule["account_share"] * rule["tracking_share"] * support
    out.update(account=rule["account"], tracking=rule["tracking"], contact=rule["contact"],
               confidence=f"{confidence:.2f}")

    direction = "RECEIVE" if float(row["amount"]) > 0 else "SPEND"
    reasons = []
    if direction != rule["type"]:
        reasons.append(f"{direction.lower()} on a payee normally {rule['type'].lower()} - refund or reversal?")
    if rule["account_share"] < 1.0:
        reasons.append(f"account split in history: {_split(rule['history']['accounts'])}")
    if rule["tracking_share"] < 1.0:
        reasons.append(f"{config.tracking_category().lower()} split in history: {_split(rule['history']['tracking'])}")
    if support < 1.0:
        reasons.append(f"only {rule['n']} prior occurrence(s)")

    if confidence >= threshold and direction == rule["type"]:
        out["needs_decision"] = "no"
        out["reason"] = f"history: {rule['n']} matching lines" + (f"; {reasons[0]}" if reasons else "")
    else:
        out["reason"] = "; ".join(reasons) or "below confidence threshold"
    return out


def write_proposal(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROPOSAL_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in PROPOSAL_FIELDS} for r in rows)


def read_proposal(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def apply_decisions(proposal_path: Path, decisions_path: Path, reviewer: str = "reviewer") -> int:
    """Apply a reviewer's decisions file (line, account, tracking, contact, note) to the proposal.

    Equivalent to editing the proposal CSV by hand; each applied line is marked
    needs_decision=no and its reason records who decided and why."""
    rows = read_proposal(proposal_path)
    with open(decisions_path, newline="") as f:
        decisions = {d["line"].strip(): d for d in csv.DictReader(f)}
    applied = 0
    for r in rows:
        d = decisions.get(r["line"])
        if not d:
            continue
        r.update(account=d["account"].strip(), tracking=d["tracking"].strip(), contact=d["contact"].strip(),
                 needs_decision="no", reason=f"decided by {reviewer}: {d.get('note', '').strip()}")
        applied += 1
    write_proposal(rows, proposal_path)
    return applied


def run(statement_path: Path, rules_path: Path, out_path: Path) -> list[dict]:
    rules = json.loads(Path(rules_path).read_text())
    rows = [classify(r, rules) for r in load_statement(statement_path)]
    write_proposal(rows, out_path)
    undecided = [r for r in rows if r["needs_decision"] == "yes"]
    print(f"Proposed {len(rows)} lines -> {out_path}")
    print(f"  auto-coded from history: {len(rows) - len(undecided)}")
    print(f"  needs a human decision:  {len(undecided)}")
    for r in undecided:
        print(f"    line {r['line']:>3}  {r['payee'][:32]:<32} {float(r['amount']):>10.2f}  "
              f"guess={r['account'] or '-'} {r['tracking'] or ''}  ({r['reason']})")
    return rows
