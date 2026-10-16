"""Blind incident drill for interview practice.

    .venv/bin/python incident.py            # declare a new incident (fault hidden)
    .venv/bin/python incident.py --reveal   # show what was actually switched on

Picks a scenario at random, applies it through the lab's own API, sends a
"healthy" run and then an "incident" run, and tells you only what a broker or
an on-call alert would tell you. Diagnose from the request log, the webhook
log and /lab/config is OFF LIMITS until you have written down your answer.

Scenarios are drawn from the lab's existing faults and releases. Nothing new
is added to the API.
"""

import argparse
import json
import random
import sys
from pathlib import Path

import httpx

from partnerflow import config, traffic

STATE = Path(__file__).with_name(".incident_answer.json")
COUNT, SEED = 120, 11

# Each scenario: a label, the lab-control calls that create it, and the one-line
# symptom a broker or an alert would report. Deliberately vague on purpose.
SCENARIOS = [
    {"name": "revoked key, one partner",
     "calls": [("PUT", "/lab/faults/auth_revoked", {"enabled": True, "partner": "cedar"})],
     "symptom": "Cedar Pay: 'None of our submissions are going through since this morning.'"},
    {"name": "server exceptions, every 4th application, all partners",
     "calls": [("PUT", "/lab/faults/server_error", {"enabled": True, "partner": "all", "every_nth": 4})],
     "symptom": "Alert: POST /applications error rate above threshold (all partners)."},
    {"name": "latency, one partner",
     "calls": [("PUT", "/lab/faults/latency", {"enabled": True, "partner": "birch", "delay_ms": 900})],
     "symptom": "Birch Books: 'Your API has got really slow, our customers are waiting on the spinner.'"},
    {"name": "rate limit, one partner",
     "calls": [("PUT", "/lab/faults/rate_limit", {"enabled": True, "partner": "alder", "limit": 15, "window_seconds": 60})],
     "symptom": "Alder Marketplace: 'We're getting rejected in bursts. Works, then stops, then works.'"},
    {"name": "webhook receiver down, one partner",
     "calls": [("PUT", "/lab/faults/webhook_failure", {"enabled": True, "partner": "cedar"})],
     "symptom": "Cedar Pay: 'Applications are being accepted but we never hear the decision.'"},
    {"name": "partner sending malformed payloads",
     "calls": [("PUT", "/lab/faults/bad_payloads", {"enabled": True, "partner": "birch", "share": 0.4})],
     "symptom": "Birch Books: 'About a third of our applications are being rejected. Nothing changed our end.'"},
    {"name": "partner referencing missing IDs",
     "calls": [("PUT", "/lab/faults/missing_resources", {"enabled": True, "partner": "birch", "share": 0.3})],
     "symptom": "Birch Books: 'Some application lookups come back not found, and some submissions too.'"},
    {"name": "release B (R-204 regression)",
     "calls": [("POST", "/lab/release", {"release": "B"})],
     "symptom": "Alert: refusal rate on POST /applications up sharply after this morning's deploy."},
    {"name": "release B plus webhook receiver down",
     "calls": [("POST", "/lab/release", {"release": "B"}),
               ("PUT", "/lab/faults/webhook_failure", {"enabled": True, "partner": "alder"})],
     "symptom": "Two tickets at once. Alder: 'decisions never arrive.' Cedar: 'more refusals than usual.'"},
    {"name": "latency plus server exceptions",
     "calls": [("PUT", "/lab/faults/latency", {"enabled": True, "partner": "all", "delay_ms": 600}),
               ("PUT", "/lab/faults/server_error", {"enabled": True, "partner": "all", "every_nth": 6})],
     "symptom": "Alert: p95 latency and 5xx rate both above threshold."},
]


def apply(api: httpx.Client, scenario: dict) -> None:
    for method, path, body in scenario["calls"]:
        response = api.request(method, path, json=body)
        response.raise_for_status()


def declare(seed: int | None) -> None:
    rng = random.Random(seed)
    scenario = rng.choice(SCENARIOS)
    with httpx.Client(base_url=config.api_url(), timeout=60) as api:
        api.post("/lab/reset").raise_for_status()
        healthy = traffic.generate("healthy", COUNT, SEED, api)
        apply(api, scenario)
        incident = traffic.generate("incident", COUNT, SEED, api)
    STATE.write_text(json.dumps({"scenario": scenario["name"], "calls": scenario["calls"]}, indent=2))
    print("INCIDENT DECLARED  (same seeded traffic in both runs: 'healthy' then 'incident')")
    print()
    print("  Report:", scenario["symptom"])
    print()
    print(f"  healthy run : {healthy['requests']} requests, {healthy['failures']} failures")
    print(f"  incident run: {incident['requests']} requests, {incident['failures']} failures")
    print()
    print("Evidence you may use (dashboard pages or curl):")
    print(f"  {config.api_url()}/lab/requests?run_label=incident&failures_only=true&limit=500")
    print(f"  {config.api_url()}/lab/requests?run_label=healthy&limit=500")
    print(f"  {config.api_url()}/lab/requests/<request_id>")
    print(f"  {config.api_url()}/lab/webhooks?failed_only=true")
    print(f"  {config.api_url()}/metrics?run_label=incident")
    print()
    print("Off limits until you have written your answer: /lab/config, the Fault injection page,")
    print("the Release simulation page, and this file's SCENARIOS list.")
    print()
    print("Write down: (1) client / server / network / auth / business-logic / release?")
    print("            (2) blast radius: which partners, which endpoint, what share")
    print("            (3) the one log field that settled it")
    print("            (4) fix or mitigation, and what you'd say to the partner")
    print("Then:  .venv/bin/python incident.py --reveal")


def reveal() -> None:
    if not STATE.exists():
        sys.exit("No incident declared yet. Run without --reveal first.")
    answer = json.loads(STATE.read_text())
    print("ANSWER:", answer["scenario"])
    for method, path, body in answer["calls"]:
        print(f"  {method} {path} {json.dumps(body)}")
    print()
    print("Now: switch it off (Fault injection page or PUT /lab/faults/<name> {\"enabled\": false},")
    print("or POST /lab/release {\"release\": \"A\"}), re-run traffic, and confirm recovery in the log.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blind incident drill against the running lab.")
    parser.add_argument("--reveal", action="store_true", help="Show the hidden scenario.")
    parser.add_argument("--seed", type=int, default=None, help="Pick the scenario deterministically.")
    args = parser.parse_args()
    reveal() if args.reveal else declare(args.seed)
