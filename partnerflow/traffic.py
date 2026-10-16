"""Seeded traffic generator: pretend partners calling the API over real HTTP.

Nothing here writes to the database. Every request goes through the API, so
the request log only ever contains events the API genuinely handled.

The same seed always produces the same journeys. That is what makes a
before/after comparison fair: the two runs send identical requests, so any
difference in the results comes from the API, not from the traffic.

Command line:
    python -m partnerflow.traffic --label before-release --count 150 --seed 7
"""

import argparse
import json
import random

import httpx

from partnerflow import config

PARTNER_WEIGHTS = {"alder": 0.5, "birch": 0.3, "cedar": 0.2}
PRODUCT_WEIGHTS = {"term_loan": 0.5, "credit_line": 0.3, "invoice_finance": 0.2}

# (lowest amount, highest amount, share of applications). Amounts are multiples
# of 500. The top band deliberately exceeds some product limits, so healthy
# traffic still contains a few legitimate R-101 rejections.
AMOUNT_BANDS = [
    (1_000, 9_500, 0.28),
    (10_000, 24_500, 0.27),
    (25_000, 50_000, 0.25),
    (50_500, 100_000, 0.15),
    (100_500, 300_000, 0.05),
]

NAME_FIRST = ["Harbour", "Maple", "Copper", "Juniper", "Saffron", "Quarry", "Lantern",
              "Meadow", "Orchard", "Pebble", "Thistle", "Willow", "Foundry", "Canal"]
NAME_SECOND = ["Bakery", "Cycles", "Joinery", "Florist", "Brewing", "Dental", "Logistics",
               "Studios", "Garage", "Catering", "Print", "Fitness", "Nursery", "Roofing"]

# Ways a partner can get a payload wrong (client-side fault B).
CORRUPTIONS = ["missing_amount", "amount_as_text", "negative_amount", "unknown_product"]

UNKNOWN_ID_START = 90_000_000   # far above any real ID in the lab


def build_journeys(seed: int, count: int) -> list[dict]:
    """Return `count` fictional customer journeys, fully decided by `seed`."""
    rng = random.Random(seed)
    journeys = []
    for index in range(count):
        partner_id = rng.choices(list(PARTNER_WEIGHTS), weights=list(PARTNER_WEIGHTS.values()))[0]
        product = rng.choices(list(PRODUCT_WEIGHTS), weights=list(PRODUCT_WEIGHTS.values()))[0]
        low, high, _ = rng.choices(AMOUNT_BANDS, weights=[band[2] for band in AMOUNT_BANDS])[0]
        amount = rng.randrange(low, high + 1, 500)
        name = f"{rng.choice(NAME_FIRST)} {rng.choice(NAME_SECOND)} Ltd"
        journeys.append({
            "index": index,
            "partner_id": partner_id,
            "customer": {
                "business_name": name,
                "contact_email": f"owner{index}@{name.split()[0].lower()}.example",
                "annual_turnover": amount * rng.randint(2, 12),
                "trading_months": rng.randint(3, 120),
            },
            "application": {
                "product": product,
                "amount": amount,
                "term_months": rng.choice([6, 12, 18, 24, 36]),
            },
            # These are drawn for every journey, whether or not a client-side
            # fault is on, so switching a fault on never changes the journeys.
            "corrupt_draw": rng.random(),
            "corruption": rng.choice(CORRUPTIONS),
            "missing_draw": rng.random(),
        })
    return journeys


def corrupt(body: dict, corruption: str) -> dict:
    """Return a copy of an application payload with one deliberate mistake."""
    broken = dict(body)
    if corruption == "missing_amount":
        del broken["amount"]
    elif corruption == "amount_as_text":
        broken["amount"] = "twenty thousand"
    elif corruption == "negative_amount":
        broken["amount"] = -abs(body["amount"])
    elif corruption == "unknown_product":
        broken["product"] = "overdraft"
    return broken


def _fault_share(faults: dict, name: str, partner_id: str) -> float:
    fault = faults[name]
    if fault["enabled"] and fault["partner"] in ("all", partner_id):
        return fault["share"]
    return 0.0


def run_traffic(client: httpx.Client, journeys: list[dict], run_label: str) -> dict:
    """Send every journey to the API and return a summary of what came back.

    Each journey is up to three requests, like a real integration:
        POST /customers  ->  POST /applications  ->  GET /applications/{id}
    A journey stops at the first request that fails.
    """
    faults = client.get("/lab/config").json()["faults"]
    summary = {"run_label": run_label, "journeys": len(journeys), "requests": 0,
               "failures": 0, "by_status_code": {},
               "applications_sent": 0, "applications_accepted": 0}

    def send(method: str, path: str, headers: dict, body: dict | None = None) -> httpx.Response:
        response = client.request(method, path, headers=headers, json=body)
        code = str(response.status_code)
        summary["requests"] += 1
        summary["failures"] += int(response.status_code >= 400)
        summary["by_status_code"][code] = summary["by_status_code"].get(code, 0) + 1
        return response

    for journey in journeys:
        partner_id = journey["partner_id"]
        headers = {
            "X-API-Key": config.PARTNER_BY_ID[partner_id]["api_key"],
            "X-Lab-Run": run_label,
            "X-Lab-Journey": str(journey["index"]),
        }
        bad_share = _fault_share(faults, "bad_payloads", partner_id)
        missing_share = _fault_share(faults, "missing_resources", partner_id)

        response = send("POST", "/customers", headers, journey["customer"])
        if response.status_code != 201:
            continue

        body = {"customer_id": response.json()["id"], **journey["application"]}
        if journey["corrupt_draw"] < bad_share:
            body = corrupt(body, journey["corruption"])
        if journey["missing_draw"] < missing_share / 2:
            body["customer_id"] = UNKNOWN_ID_START + journey["index"]

        summary["applications_sent"] += 1
        response = send("POST", "/applications", headers, body)
        if response.status_code != 201:
            continue
        summary["applications_accepted"] += 1

        application_id = response.json()["id"]
        if missing_share / 2 <= journey["missing_draw"] < missing_share:
            application_id = UNKNOWN_ID_START + journey["index"]
        send("GET", f"/applications/{application_id}", headers)

    summary["failure_rate"] = round(summary["failures"] / summary["requests"], 4) if summary["requests"] else None
    summary["application_acceptance_rate"] = (
        round(summary["applications_accepted"] / summary["applications_sent"], 4)
        if summary["applications_sent"] else None
    )
    summary["by_status_code"] = dict(sorted(summary["by_status_code"].items()))
    return summary


def generate(run_label: str, count: int = 150, seed: int = 7,
             client: httpx.Client | None = None) -> dict:
    """Build journeys from the seed and send them. Opens a client if none is given."""
    journeys = build_journeys(seed, count)
    if client is not None:
        return run_traffic(client, journeys, run_label)
    with httpx.Client(base_url=config.api_url(), timeout=30) as new_client:
        return run_traffic(new_client, journeys, run_label)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send seeded traffic to the PartnerFlow Lab API.")
    parser.add_argument("--label", required=True, help="Name for this run, e.g. before-release")
    parser.add_argument("--count", type=int, default=150, help="Number of customer journeys")
    parser.add_argument("--seed", type=int, default=7, help="Same seed = same traffic")
    args = parser.parse_args()
    print(json.dumps(generate(args.label, args.count, args.seed), indent=2))
