"""Command line: learn -> propose -> (human review) -> post -> check.

    py -m cardcoding --demo                     full walkthrough, offline, mocked Xero (same as `demo`)
    py -m cardcoding pull-history               fetch 12 months of coded card history from Xero
    py -m cardcoding learn   [--demo]           build payee rules from history
    py -m cardcoding propose [--demo]           write the proposal CSV for review
    py -m cardcoding decide  --file F           apply a reviewer's decisions file (optional; or edit the CSV)
    py -m cardcoding post    [--post] [--demo]  dry run by default; --post writes to Xero
    py -m cardcoding check   [--demo]           reconciled vs waiting

--demo uses the bundled synthetic fixtures and an in-memory mock of Xero.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from . import check, config, learn, post, propose
from .mock_xero import FakeXero


def _paths(args) -> dict:
    out = Path(args.out or (config.OUTPUT / ("demo" if args.demo else "")))
    return {
        "history": Path(args.history or (config.FIXTURES / "history_sample.json" if args.demo else "data/history.json")),
        "statement": Path(args.statement or (config.FIXTURES / "statement_sample.csv" if args.demo else "data/statement.csv")),
        "rules": out / "rules.json",
        "proposal": out / "proposal.csv",
        "log": out / "posted_log.csv",
        "mock_state": out / "mock_xero_state.json",
    }


def _need(path: Path, hint: str) -> None:
    if not Path(path).exists():
        raise SystemExit(f"Missing {path}. {hint}")


def _client(args, paths):
    if args.demo:
        return FakeXero(state_file=paths["mock_state"])
    from .xero_api import XeroClient

    return XeroClient.from_env()


def cmd_pull_history(args, paths):
    if args.demo:
        raise SystemExit("pull-history talks to a real Xero org; the demo uses fixtures/history_sample.json instead.")
    client = _client(args, paths)
    acct = config.card_account_id(args.demo)
    since = date.today() - timedelta(days=365)
    where = f'BankAccount.AccountID==guid("{acct}") AND Date>=DateTime({since.year},{since.month},{since.day})'
    txns, page = [], 1
    while True:
        batch = client.call("GET", "BankTransactions", params={"where": where, "page": page})["BankTransactions"]
        txns.extend(batch)
        if len(batch) < check.PAGE_SIZE:
            break
        page += 1
    paths["history"].parent.mkdir(parents=True, exist_ok=True)
    paths["history"].write_text(json.dumps({"BankTransactions": txns}, indent=1))
    print(f"Saved {len(txns)} transactions since {since} -> {paths['history']}")


def cmd_demo(args, paths):
    for p in ("proposal", "log", "mock_state", "rules"):
        paths[p].unlink(missing_ok=True)
    acct = config.card_account_id(demo=True)
    xero = FakeXero(state_file=paths["mock_state"])

    def step(title):
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")

    step("1. LEARN - build payee rules from 12 months of coded history")
    learn.run(paths["history"], paths["rules"])

    step("2. PROPOSE - code this month's statement; flag anything uncertain")
    propose.run(paths["statement"], paths["rules"], paths["proposal"])

    step("3. TRY TO POST BEFORE REVIEW - the approval gate refuses")
    try:
        post.run(paths["proposal"], paths["log"], acct, client=xero, do_post=True)
    except post.ApprovalRequired as e:
        print(f"-> {e}")
    print(f"-> API write calls made: {len(xero.writes)}")

    step("4. HUMAN REVIEW - reviewer decides the flagged lines (fixtures/demo_decisions.csv)")
    n = propose.apply_decisions(paths["proposal"], config.FIXTURES / "demo_decisions.csv", reviewer="Jane Doe")
    print(f"Applied {n} decisions to {paths['proposal']}")

    step("5. DRY RUN - show exactly what would be created")
    post.run(paths["proposal"], paths["log"], acct, client=xero, do_post=False)
    print(f"-> API write calls made: {len(xero.writes)}")

    step("6. POST - write to (mock) Xero, log every BankTransactionID")
    post.run(paths["proposal"], paths["log"], acct, client=xero, do_post=True)

    step("7. RE-RUN POST - idempotent, nothing is double-posted")
    post.run(paths["proposal"], paths["log"], acct, client=xero, do_post=True)

    step("8. CHECK - after the bookkeeper reconciles some lines in Xero")
    xero.reconcile(every=2)
    check.run(paths["log"], acct, xero)
    print(f"\nDemo artifacts in {paths['proposal'].parent}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cardcoding", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["demo", "pull-history", "learn", "propose", "decide", "post", "check"])
    ap.add_argument("--demo", action="store_true", help="synthetic fixtures + mocked Xero, fully offline")
    ap.add_argument("--post", action="store_true", help="actually create transactions (default is a dry run)")
    ap.add_argument("--only", help="comma-separated statement line numbers to limit a post")
    ap.add_argument("--file", help="decisions CSV for the decide command")
    ap.add_argument("--reviewer", default="reviewer", help="name recorded on decided lines")
    ap.add_argument("--history")
    ap.add_argument("--statement")
    ap.add_argument("--out", help="output folder (default output/ or output/demo/)")
    args = ap.parse_args(argv)
    if args.command is None:
        if not args.demo:
            ap.error("a command is required (or pass --demo for the full offline walkthrough)")
        args.command = "demo"  # `py -m cardcoding --demo` runs the scripted demo
    if not args.demo and args.command != "demo":
        config.load_dotenv()
    if args.command == "demo":
        args.demo = True
    paths = _paths(args)

    if args.command == "demo":
        cmd_demo(args, paths)
    elif args.command == "pull-history":
        cmd_pull_history(args, paths)
    elif args.command == "learn":
        _need(paths["history"], "Run pull-history first (or pass --history).")
        learn.run(paths["history"], paths["rules"])
    elif args.command == "propose":
        _need(paths["rules"], "Run learn first.")
        _need(paths["statement"], "Save the statement CSV there (or pass --statement).")
        propose.run(paths["statement"], paths["rules"], paths["proposal"])
    elif args.command in ("decide", "post"):
        _need(paths["proposal"], "Run propose first.")
    if args.command == "decide":
        if not args.file:
            ap.error("decide needs --file")
        print(f"Applied {propose.apply_decisions(paths['proposal'], Path(args.file), args.reviewer)} decisions")
    elif args.command == "post":
        acct = config.card_account_id(args.demo)
        client = _client(args, paths) if args.post else None  # dry run never builds a client
        only = set(args.only.split(",")) if args.only else None
        post.run(paths["proposal"], paths["log"], acct, client=client, do_post=args.post, only=only)
    elif args.command == "check":
        check.run(paths["log"], config.card_account_id(args.demo), _client(args, paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
