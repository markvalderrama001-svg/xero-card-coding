"""Learn payee -> (account, tracking option, contact) rules from coded GL history.

Input is a Xero BankTransactions export for the card account (the JSON shape
returned by ``GET BankTransactions``), typically the trailing 12 months.
Output is a rules file the classifier reads. Nothing is hard-coded: if the
books change, re-run ``learn``.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import config

# Card processors that prefix the real merchant name on statements.
PROCESSOR_PREFIXES = {"SQ", "TST", "SP", "PAYPAL", "PP", "WWW"}


def payee_key(text: str, width: int = 2) -> str:
    """Normalise a bank/statement payee into a stable matching key.

    'ACME SUPPLIES*SUB 0042 SEATTLE WA' -> 'ACME SUPPLIES'
    'SQ *BLUE BOTTLE CAFE 118'          -> 'BLUE BOTTLE'
    """
    tokens = re.sub(r"[^A-Z0-9]+", " ", text.upper()).split()
    while tokens and tokens[0] in PROCESSOR_PREFIXES:
        tokens = tokens[1:]
    words = []
    for t in tokens:
        if any(ch.isdigit() for ch in t):
            break
        words.append(t)
        if len(words) == width:
            break
    return " ".join(words)


def _tracking_option(line: dict, category: str) -> str:
    for t in line.get("Tracking") or []:
        if t.get("Name") == category:
            return t.get("Option", "")
    return ""


def learn(history: list[dict], category: str | None = None) -> dict:
    """Return {payee_key: rule}. Each rule records the majority coding and how dominant it is."""
    category = category or config.tracking_category()
    seen: dict[str, dict] = defaultdict(lambda: {
        "accounts": Counter(), "tracking": Counter(), "contacts": Counter(), "types": Counter(), "n": 0})
    for txn in history:
        if txn.get("Status") == "DELETED":
            continue
        for line in txn.get("LineItems") or []:
            key = payee_key(line.get("Description") or (txn.get("Contact") or {}).get("Name", ""))
            if not key or not line.get("AccountCode"):
                continue
            s = seen[key]
            s["n"] += 1
            s["accounts"][line["AccountCode"]] += 1
            s["tracking"][_tracking_option(line, category)] += 1
            s["contacts"][(txn.get("Contact") or {}).get("Name", "")] += 1
            s["types"][txn.get("Type", "SPEND")] += 1

    rules = {}
    for key, s in sorted(seen.items()):
        account, a_n = s["accounts"].most_common(1)[0]
        tracking, t_n = s["tracking"].most_common(1)[0]
        rules[key] = {
            "account": account,
            "account_share": round(a_n / s["n"], 3),
            "tracking": "" if account in config.untracked_accounts() else tracking,
            "tracking_share": 1.0 if account in config.untracked_accounts() else round(t_n / s["n"], 3),
            "contact": s["contacts"].most_common(1)[0][0],
            "type": s["types"].most_common(1)[0][0],
            "n": s["n"],
            "history": {
                "accounts": dict(s["accounts"]),
                "tracking": {k or "(none)": v for k, v in s["tracking"].items()},
            },
        }
    return rules


def load_history(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text())
    return data["BankTransactions"] if isinstance(data, dict) else data


def run(history_path: Path, rules_path: Path) -> dict:
    rules = learn(load_history(history_path))
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(rules, indent=2))
    print(f"Learned {len(rules)} payee rules from {history_path.name} -> {rules_path}")
    return rules
