"""Post an approved proposal to Xero as spend/receive-money BankTransactions.

Controls, in order:
  1. Approval gate  - refuses to post while ANY line in the proposal still has
                      needs_decision != no, or a decided line is incomplete.
  2. Dry run        - default. Prints exactly what would be created; makes no API calls.
  3. Idempotency    - lines already in the posted log, or already tagged in Xero,
                      are skipped, so a re-run never double-posts.
  4. Traceability   - every transaction carries Reference "CC rec <line>" and its
                      BankTransactionID is appended to the posted log, so a batch
                      can be found, reconciled or voided later.
Transactions are created unreconciled; a human still matches them in Xero's
bank reconciliation screen.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .check import fetch_tagged
from .propose import SKIP, read_proposal

LOG_FIELDS = ["line", "reference", "BankTransactionID", "date", "amount", "account", "tracking",
              "contact", "status", "posted_at"]
BATCH_SIZE = 50  # Xero recommends <= 50 transactions per request


class ApprovalRequired(SystemExit):
    """Raised when the proposal is not fully decided. Subclasses SystemExit so the CLI exits non-zero."""


def reference(line: str) -> str:
    return f"{config.REFERENCE_PREFIX}{line}"


def read_log(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def gate(rows: list[dict]) -> list[str]:
    """Return human-readable problems that block posting. Empty list = clear to post."""
    problems = []
    for r in rows:
        if r["account"] == SKIP:
            continue
        if r["needs_decision"].strip().lower() != "no":
            problems.append(f"line {r['line']}: still needs a decision ({r['reason']})")
            continue
        if not r["account"]:
            problems.append(f"line {r['line']}: no account code")
        if not r["contact"]:
            problems.append(f"line {r['line']}: no contact")
        if r["account"] not in config.untracked_accounts() and not r["tracking"]:
            problems.append(f"line {r['line']}: no {config.tracking_category()} option")
    return problems


def build_transaction(r: dict, card_account_id: str) -> dict:
    amount = float(r["amount"])
    line_item: dict[str, Any] = {
        "Description": r["payee"], "Quantity": 1, "UnitAmount": round(abs(amount), 2),
        "AccountCode": r["account"], "TaxType": "NONE",
    }
    if r["tracking"] and r["account"] not in config.untracked_accounts():
        line_item["Tracking"] = [{"Name": config.tracking_category(), "Option": r["tracking"]}]
    return {
        "Type": "RECEIVE" if amount > 0 else "SPEND",
        "Contact": {"Name": r["contact"]},  # matched by name, so history's contact is reused, not duplicated
        "BankAccount": {"AccountID": card_account_id},
        "Date": r["date"],
        "Reference": reference(r["line"]),
        "LineAmountTypes": "NoTax",
        "LineItems": [line_item],
    }


def run(proposal_path: Path, log_path: Path, card_account_id: str, client: Any = None,
        do_post: bool = False, only: set[str] | None = None) -> dict:
    rows = read_proposal(proposal_path)
    problems = gate(rows)
    if problems:
        print("BLOCKED - the proposal is not fully approved:")
        for p in problems:
            print("  " + p)
        if do_post:
            raise ApprovalRequired(f"Refusing to post: {len(problems)} line(s) need a human decision.")
        print("(dry run continues for the decided lines only)\n")

    done = {r["line"] for r in read_log(log_path)}
    selected = [r for r in rows if r["account"] != SKIP and r["needs_decision"].strip().lower() == "no"]
    if only:
        selected = [r for r in selected if r["line"] in only]
    pending = [r for r in selected if r["line"] not in done]
    skipped_logged = len(selected) - len(pending)

    skipped_xero = 0
    if do_post and pending:
        tagged = {t["Reference"] for t in fetch_tagged(client, card_account_id)}
        before = len(pending)
        pending = [r for r in pending if reference(r["line"]) not in tagged]
        skipped_xero = before - len(pending)

    txns = [build_transaction(r, card_account_id) for r in pending]
    for r, t in zip(pending, txns):
        print(f"{r['line']:>4} {r['date']} {t['Type']:<7} {r['contact'][:28]:<28} {float(r['amount']):>10.2f}  "
              f"{r['account']} {r['tracking']}  [{t['Reference']}]")
    net = sum(float(r["amount"]) for r in pending)
    print(f"\n{len(txns)} transaction(s), net {net:,.2f}"
          f"  (skipped: {skipped_logged} already in log, {skipped_xero} already tagged in Xero, "
          f"{sum(1 for r in rows if r['account'] == SKIP)} marked SKIP)")

    summary = {"would_post": len(txns), "posted": 0, "errors": 0, "skipped_logged": skipped_logged,
               "skipped_xero": skipped_xero, "blocked": problems}
    if not do_post or not txns:
        print("(dry run - nothing created)" if not do_post else "Nothing new to post.")
        return summary

    log_path.parent.mkdir(parents=True, exist_ok=True)
    new_log = not log_path.exists()
    with open(log_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_log:
            w.writeheader()
        for start in range(0, len(txns), BATCH_SIZE):
            chunk_rows, chunk = pending[start:start + BATCH_SIZE], txns[start:start + BATCH_SIZE]
            res = client.call("PUT", "BankTransactions", params={"summarizeErrors": "false"},
                              body={"BankTransactions": chunk})["BankTransactions"]
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r, t in zip(chunk_rows, res):
                if t.get("HasErrors") or t.get("ValidationErrors"):
                    summary["errors"] += 1  # not logged, so the next run retries it
                    print(f"  line {r['line']} rejected: {t.get('ValidationErrors')}")
                    continue
                w.writerow({"line": r["line"], "reference": reference(r["line"]),
                            "BankTransactionID": t["BankTransactionID"], "date": r["date"], "amount": r["amount"],
                            "account": r["account"], "tracking": r["tracking"], "contact": r["contact"],
                            "status": t.get("Status", ""), "posted_at": stamp})
                summary["posted"] += 1
    print(f"Created {summary['posted']}; errors: {summary['errors']}. Log: {log_path}")
    return summary
