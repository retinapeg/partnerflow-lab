"""Reset PartnerFlow Lab: delete recorded data, restore release v1.0.0 and no faults.

Usage:
    python reset.py                 full reset
    python reset.py --keep-config   delete data but keep the release and fault settings
"""

import argparse

import httpx

from partnerflow import config, db


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset PartnerFlow Lab.")
    parser.add_argument("--keep-config", action="store_true",
                        help="keep the current release and fault settings")
    args = parser.parse_args()

    # If the API is running, ask it to reset: that also clears its in-memory
    # counters (rate-limit windows and the every-Nth error counter).
    try:
        response = httpx.post(f"{config.api_url()}/lab/reset",
                              json={"keep_config": args.keep_config}, timeout=5)
        response.raise_for_status()
        print(f"Reset through the running API. Release {response.json()['version']}.")
    except httpx.HTTPError:
        db.reset_data(keep_config=args.keep_config)
        print(f"API not running, so the database file was reset directly: {config.db_path()}")


if __name__ == "__main__":
    main()
