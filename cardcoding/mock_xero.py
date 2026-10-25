"""An in-memory stand-in for the Xero API, used by --demo and the tests.

It implements the same ``call(method, endpoint, params, body)`` interface as
XeroClient for the endpoints this tool uses, validates account codes and
tracking options like Xero would, and can persist its state to a JSON file so
separate CLI commands (post, then check) see the same "org".
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

DEMO_ACCOUNTS = {"3100", "4900", "6100", "6200", "6300", "6400", "6500", "6600", "6700"}
DEMO_TRACKING = {"Brand": {"Northwind Studio", "Bluebird Goods", "Summit Media"}}


class FakeXero:
    def __init__(self, state_file: Path | None = None, page_size: int = 100):
        self.state_file = Path(state_file) if state_file else None
        self.page_size = page_size
        self.calls: list[tuple[str, str]] = []
        self.txns: list[dict] = []
        if self.state_file and self.state_file.exists():
            self.txns = json.loads(self.state_file.read_text())

    def _save(self):
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.txns, indent=2))

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [c for c in self.calls if c[0] in ("PUT", "POST", "DELETE")]

    def call(self, method: str, endpoint: str, params: dict | None = None, body=None) -> dict:
        method = method.upper()
        self.calls.append((method, endpoint))
        if endpoint == "BankTransactions" and method == "GET":
            return {"BankTransactions": self._query((params or {}).get("where", ""), int((params or {}).get("page", 1)))}
        if endpoint == "BankTransactions" and method in ("PUT", "POST"):
            return {"BankTransactions": [self._create(t) for t in body["BankTransactions"]]}
        if endpoint == "Organisation":
            return {"Organisations": [{"Name": "E100 Demo Co (mock)"}]}
        raise NotImplementedError(f"FakeXero does not implement {method} {endpoint}")

    def _query(self, where: str, page: int) -> list[dict]:
        acct = re.search(r'AccountID==guid\("([^"]+)"\)', where)
        prefix = re.search(r'Reference\.StartsWith\("([^"]+)"\)', where)
        rows = [t for t in self.txns
                if (not acct or t["BankAccount"]["AccountID"] == acct.group(1))
                and (not prefix or (t.get("Reference") or "").startswith(prefix.group(1)))]
        start = (page - 1) * self.page_size
        return rows[start:start + self.page_size]

    def _create(self, t: dict) -> dict:
        errors = []
        for li in t.get("LineItems", []):
            if li.get("AccountCode") not in DEMO_ACCOUNTS:
                errors.append({"Message": f"Account code '{li.get('AccountCode')}' is not a valid code for this document."})
            for tr in li.get("Tracking") or []:
                if tr.get("Option") not in DEMO_TRACKING.get(tr.get("Name"), set()):
                    errors.append({"Message": f"Tracking option '{tr.get('Option')}' is not valid."})
        if errors:
            return dict(t, HasErrors=True, ValidationErrors=errors)
        created = dict(t, BankTransactionID=str(uuid.uuid4()), Status="AUTHORISED", IsReconciled=False)
        self.txns.append(created)
        self._save()
        return created

    def reconcile(self, every: int = 2) -> int:
        """Simulate a human reconciling some lines in Xero's bank rec screen."""
        n = 0
        for i, t in enumerate(self.txns):
            if i % every == 0 and not t["IsReconciled"]:
                t["IsReconciled"] = True
                n += 1
        self._save()
        return n
