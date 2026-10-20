"""Run the recurring-issue triage agent from the terminal.

    .venv/bin/python triage.py                 # one pass: decide, act, log
    .venv/bin/python triage.py --show          # just print the registry
    .venv/bin/python triage.py --dry-run       # decide and log, never act
    .venv/bin/python triage.py --watch 30      # keep running every 30 s (Ctrl+C stops)
    .venv/bin/python triage.py --mode llm      # Claude writes the message (needs ANTHROPIC_API_KEY on the API)
    .venv/bin/python triage.py --all           # include closed and already-handled episodes

Everything goes through the running lab's API (POST /lab/triage), so this is the
same agent the dashboard runs, and the same audit log.
"""

import argparse
import sys
import time

import httpx

from partnerflow import config


def show_registry(api: httpx.Client) -> None:
    data = api.get("/lab/recurring").json()
    rows = data["registry"]
    if not rows:
        print("No failures recorded yet. Send traffic with a fault on, then try again.")
        return
    print(f"{'fingerprint':46} {'status':10} {'ep':>2} {'x/n':>8} {'baseline':>9} {'tail p':>9}  owner")
    for row in rows:
        tail = "n/a" if row["tail_p"] is None else f"{row['tail_p']:.2g}"
        print(f"{row['fingerprint']:46} {row['status']:10} {row['episodes']:>2} "
              f"{row['latest_occurrences']:>3}/{row['latest_exposed']:<4} {row['baseline_rate'] * 100:>8.2f}% "
              f"{tail:>9}  {row['owner']}")


def run_once(api: httpx.Client, args) -> None:
    body = {"mode": args.mode, "dry_run": args.dry_run, "force": args.all}
    result = api.post("/lab/triage", json=body).json()
    stamp = result["ran_at"][11:19]
    if not result["triaged"] and not result.get("followups"):
        why = ", ".join(sorted({s["why"] for s in result["skipped"]})) or "no failure fingerprints"
        print(f"[{stamp}] nothing to do ({why})")
        return
    for entry in result["triaged"]:
        decision = entry["decision"]
        print(f"[{stamp}] {entry['fingerprint']}  ->  {decision['status'].upper()} "
              f"({decision['severity']}, owner {decision['owner']}, {decision['blast_radius']})")
        print(f"    {decision['summary']}")
        for action in entry["actions"]:
            extra = {k: v for k, v in action.items() if k not in ("action", "result")}
            print(f"    action: {action['action']} -> {action['result']} {extra if extra else ''}")
        if decision["partner_message"]:
            print("    message to partner:")
            for line in decision["partner_message"].splitlines():
                print(f"      {line}")
    for followup in result.get("followups", []):
        if "fingerprint" in followup:
            print(f"[{stamp}] {followup['fingerprint']}  ->  follow-up: {followup['decision']['summary']}")
        else:
            print(f"[{stamp}] webhook replay: {followup['result']} "
                  f"({followup['recovered']} recovered, {followup['still_failed']} still failing)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage recurring API issues through the lab's API.")
    parser.add_argument("--show", action="store_true", help="print the registry and exit")
    parser.add_argument("--dry-run", action="store_true", help="decide and log, but take no action")
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules")
    parser.add_argument("--all", action="store_true", help="also closed and already-handled episodes")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="keep running every N seconds")
    args = parser.parse_args()

    with httpx.Client(base_url=config.api_url(), timeout=120) as api:
        try:
            api.get("/health").raise_for_status()
        except httpx.HTTPError:
            sys.exit(f"The lab API is not answering at {config.api_url()}. Start it with ./start.sh")
        if args.show:
            show_registry(api)
            return
        run_once(api, args)
        while args.watch:
            time.sleep(args.watch)
            run_once(api, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
