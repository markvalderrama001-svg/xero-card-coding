"""Settings. Everything org-specific comes from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
OUTPUT = ROOT / "output"

DEMO_CARD_ACCOUNT_ID = "00000000-0000-4000-8000-00000000c0de"  # synthetic demo bank account id


def confidence_threshold() -> float:
    """Lines below this confidence go to a human instead of being auto-proposed."""
    return float(os.environ.get("CARD_CONFIDENCE_THRESHOLD", "0.8"))


def untracked_accounts() -> set[str]:
    """Accounts that never carry a tracking option (e.g. owner's draw is not a brand cost)."""
    return {a.strip() for a in os.environ.get("CARD_UNTRACKED_ACCOUNTS", "3100").split(",") if a.strip()}


REFERENCE_PREFIX = "CC rec "


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a local, gitignored .env into the environment (existing vars win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tracking_category() -> str:
    return os.environ.get("XERO_TRACKING_CATEGORY", "Brand")


def card_account_id(demo: bool = False) -> str:
    if demo:
        return DEMO_CARD_ACCOUNT_ID
    value = os.environ.get("XERO_CARD_ACCOUNT_ID", "").strip()
    if not value:
        raise SystemExit("Set XERO_CARD_ACCOUNT_ID to the Xero AccountID of the credit-card bank account (see .env.example).")
    return value
