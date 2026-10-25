"""Report which posted transactions have been reconciled in Xero and which are still waiting."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from . import config

PAGE_SIZE = 100  # Xero returns up to 100 BankTransactions per page


def fetch_tagged(client: Any, card_account_id: str) -> list[dict]:
    """All BankTransactions on the card account whose Reference starts with our tag (paginated)."""
    where = (f'BankAccount.AccountID==guid("{card_account_id}") AND Reference != null '
             f'AND Reference.StartsWith("{config.REFERENCE_PREFIX}")')
    found, page = [], 1
    while True:
        batch = client.call("GET", "BankTransactions", params={"where": where, "page": page})["BankTransactions"]
        found.extend(batch)
        if len(batch) < PAGE_SIZE:
            return found
        page += 1


def run(log_path: Path, card_account_id: str, client: Any) -> dict:
    if not Path(log_path).exists():
        print(f"No posted log at {log_path} - nothing to check.")
        return {"posted": 0, "reconciled": 0, "waiting": [], "missing": []}
    with open(log_path, newline="") as f:
        rows = list(csv.DictReader(f))
    live = {t["BankTransactionID"]: t for t in fetch_tagged(client, card_account_id)}
    reconciled = [r for r in rows if live.get(r["BankTransactionID"], {}).get("IsReconciled")]
    missing = [r["line"] for r in rows if r["BankTransactionID"] not in live
               or live[r["BankTransactionID"]].get("Status") == "DELETED"]
    waiting = [r["line"] for r in rows if r not in reconciled and r["line"] not in missing]
    print(f"Posted: {len(rows)}  reconciled: {len(reconciled)}  waiting: {len(waiting)}  missing/voided: {len(missing)}")
    if waiting:
        print("Waiting line #s:", ",".join(waiting))
    if missing:
        print("Not found in Xero (voided or deleted?):", ",".join(missing))
    return {"posted": len(rows), "reconciled": len(reconciled), "waiting": waiting, "missing": missing}
