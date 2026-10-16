"""The release story from the command line, against the running API.

Usage:   python demo.py            (start the lab first with ./start.sh)

Does what the dashboard's "Release simulation" page does, and prints the
numbers. Useful as a rehearsal, and as a fallback if a browser misbehaves.
"""

import httpx

from partnerflow import analytics, config, traffic

COUNT, SEED = 150, 7
ELIGIBLE = [p for p in config.PRODUCTS if p != config.R204_PRODUCT]


def main() -> None:
    with httpx.Client(base_url=config.api_url(), timeout=30) as api:
        api.post("/lab/reset")   # start from a clean lab so the numbers are repeatable
        print("1. Healthy API:", api.get("/health").json())

        print("2. Traffic on v1.0.0 ...")
        traffic.generate("before-release", COUNT, SEED, api)
        print("3. Deploy v1.1.0:", api.post("/lab/release", json={"release": "B"}).json()["version"])
        traffic.generate("after-release", COUNT, SEED, api)
        print("4. Apply patch:", api.post("/lab/hotfix", json={"enabled": True}).json()["version"])
        traffic.generate("after-fix", COUNT, SEED, api)

    log = analytics.load_requests()
    posts = log[log["request"] == "POST /applications"]

    print("\nPOST /applications, identical traffic in each run:")
    print(f"{'run':<16}{'release':<10}{'sent':>6}{'refused':>9}{'rate':>8}"
          f"{'eligible refused':>18}{'invoice refused':>17}")
    for label in ("before-release", "after-release", "after-fix"):
        run = posts[posts["run_label"] == label]
        eligible = run[run["product"].isin(ELIGIBLE)]
        invoice = run[run["product"] == config.R204_PRODUCT]
        print(f"{label:<16}{run['release_version'].iloc[0]:<10}{len(run):>6}"
              f"{int(run['failed'].sum()):>9}{run['failed'].mean():>8.1%}"
              f"{int(eligible['failed'].sum()):>18}{int(invoice['failed'].sum()):>17}")

    change = analytics.compare_failure_rates(posts, "run_label", "before-release", "after-release")
    print(f"\nRegression: {change['difference'] * 100:+.1f} points "
          f"(95% interval {change['ci_low'] * 100:+.1f} to {change['ci_high'] * 100:+.1f}, "
          f"p = {change['p_value']:.2g})")

    broke = analytics.paired_flips(log, "before-release", "after-release")
    print(f"Requests accepted before and refused after the release: {len(broke)}")
    print("  by product:", broke["product"].value_counts().to_dict())
    print("  reasons:   ", broke["failure_reason_after"].value_counts().to_dict())
    print(f"  amounts:    {int(broke['amount'].min()):,} to {int(broke['amount'].max()):,} GBP")

    left = analytics.paired_flips(log, "before-release", "after-fix")
    print(f"Still refused after the fix: {len(left)}", left["product"].value_counts().to_dict(),
          "(the intended rule)")


if __name__ == "__main__":
    main()
