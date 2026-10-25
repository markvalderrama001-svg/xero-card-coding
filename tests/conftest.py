import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cardcoding import config, learn, propose  # noqa: E402

FIX = ROOT / "fixtures"
CARD = config.DEMO_CARD_ACCOUNT_ID


@pytest.fixture
def rules():
    return learn.learn(learn.load_history(FIX / "history_sample.json"), category="Brand")


@pytest.fixture
def proposal(tmp_path, rules):
    """A freshly proposed (not yet reviewed) proposal CSV in a temp folder."""
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(rules))
    path = tmp_path / "proposal.csv"
    propose.run(FIX / "statement_sample.csv", rules_path, path)
    return path


@pytest.fixture
def approved(proposal):
    """The same proposal after the reviewer applied the demo decisions."""
    propose.apply_decisions(proposal, FIX / "demo_decisions.csv", reviewer="Jane Doe")
    return proposal
