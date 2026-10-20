# Three-minute demo

> Synthetic data. Fictional lending simulation. Not affiliated with iwoca.

Each row is: what to click, what appears, and the one fact worth saying. The numbers are
what the lab produces with its defaults (150 journeys, seed 7). They are the same every
time after a reset, so you can rehearse against them.

## Before you start

1. `./start.sh` and wait for "PartnerFlow Lab is running".
2. Open http://127.0.0.1:8510.
3. Sidebar → **Reset the lab** → **Reset everything**.

If the browser misbehaves, `.venv/bin/python demo.py` prints the same story in the terminal.

## The demo

| Time | Page and click | What appears | The fact to state |
|---|---|---|---|
| 0:00 | **Overview** | API status "Up", release v1.0.0, 0 requests | The dashboard is only a client. It asked the API's `/health` endpoint. |
| 0:15 | **API playground** → *Send request* | `POST /customers` returns 201 with customer id 1 | The request went over HTTP to the API, which stored a row in SQLite. |
| 0:25 | Request → `POST /applications` → *Send request* | 201, `"status": "approved"`, an X-Request-ID | Every response carries a request ID. "What the API recorded" shows the log row for it. |
| 0:45 | **Release simulation** → *Generate 'before-release' traffic* | 150 applications sent, 6 refused (4.0%) | The baseline. Those 6 are legitimate: amounts over the product limit. |
| 0:55 | *Activate release v1.1.0* | Release notes: R-204 applies to `invoice_finance` only | The notes are the claim. The log will show what actually happened. |
| 1:05 | *Generate 'after-release' traffic* | Refused 4.0% → 27.3%, change +23.3 pts, 95% interval +15.5 to +31.1 | Same seed, so identical requests. The only thing that changed is the release. |
| 1:25 | Step 5 table, open one request | 35 applications accepted before, refused after. All `rule_R204_amount_band` | Because the traffic is identical, runs can be matched request by request. |
| 1:45 | Step 6 grid | The 25k-50k column is 100% refused for all three products | The notes said one product. The data shows three. That mismatch is the bug. |
| 2:00 | *Read the rule's source code* | The `if hotfix:` branch checks the product. The `else:` branch does not | The cause, in one line of `rules.py`. |
| 2:10 | Step 7 → *Apply patch v1.1.1* | Sidebar shows v1.1.1 | Rolling back or disabling the rule would remove an intended control. The patch fixes only the bug. |
| 2:25 | *Generate 'after-fix' traffic* | Term loans and credit lines: 3 → 28 → 3 refused. Invoice finance: 3 → 13 → 13 | Eligible products are back to baseline. The intended rule is still enforced. |
| 2:40 | **Data science** → sections 3 and 4 | Naive −18.0 pts, adjusted −11.0, true −12.2. A/B: +5.5 pts, interval +3.9 to +7.2 | The naive figure is a correlation inflated by application size. The A/B test is randomised, so its difference is causal. |

## If you have another minute

- **Fault injection** → switch on *A. Authentication failure* → *Send POST /customers*
  as cedar → 401 `api_key_revoked`. Switch it off → 201. The switch changed the API's
  behaviour, not the dashboard's display: the same curl command gives the same result.
- **Incident investigation** → section 4 → click any failed row. You get the full
  record, a plain-English explanation of the status code, the rest of that customer's
  journey, and the identical request in the other runs.

- **Recurring issues** (the "same ticket every fortnight" minute): pick *Revoked API key
  (cedar) comes back after a fix* → **Replay it**. The table shows `cedar · 401` with two
  episodes, status *active*, and the intended rule R-101 sitting in *watch/background* because
  each of its episodes is in line with the history before it. **Run the agent on this issue**:
  ACT, owner us, the partner message with the numbers in it, an incident opened, the evidence
  pack in the audit log. "It only acts on flagged episodes, only with the runbook's one action,
  and never touches a switch."

- **Broker monitoring** (the data-science minute): "Rows stored 1,680, requests
  represented ~1,000,000." Section 2: the control chart for Meridian with eight red dots on
  day 10; "1,680 hours tested, 2.3 false flags expected by chance." Section 4: the evidence
  panel (14.4% vs 3.9% expected, 1,003 excess failures, timeouts 45% → 84% of failures,
  p95 latency 460 → 1,170 ms, other brokers flat). Section 5: before-vs-during −10.9 pts,
  difference-in-differences −11.5 pts, true effect −9.6 pts.

## Questions to be ready for, and where the answer lives

| Question | Where to look |
|---|---|
| How does switching a release in the dashboard change the API? | `set_release()` in main.py writes to the `lab_config` table. `record_request()` reads that table on every request. |
| How do you know a failure is the API's fault or the caller's? | Status class: 4xx caller, 5xx API. Then `failure_reason`. README table "Where different HTTP errors originate". |
| Why 422 and not 400 for a refused application? | The JSON was understood but could not be processed. FastAPI uses 422 for validation, and the rules follow the same convention. |
| Why is the comparison fair? | `build_journeys()` in traffic.py: same seed, same journeys. `test_same_seed_gives_identical_journeys` proves it. |
| What does the 95% interval mean? | If the experiment were repeated many times, about 95% of intervals built this way would contain the true effect. `compare_proportions()` in analytics.py. |
| How was the fix verified? | `test_regression_is_resolved_by_the_targeted_fix` in tests/test_release.py checks both directions: eligible products restored, intended rule still enforced. |
| What would you change for production? | README "Known limitations": hashed keys, HTTPS, asynchronous logging, real webhook delivery with backoff, a shared rate-limit store. |
| How do you stop the same issue costing an hour every time? | *Recurring issues*: fingerprint → episodes → each episode tested against the history before it (recognised incidents left out) → the agent acts within the runbook and logs the evidence. `recurrence.py`, `runbooks.py`, `triage.py`. |
| Why doesn't the agent flag the intended rule R-101? | Its refusals are in the baseline: a steady share, never distinct from the history before each episode. `test_a_steady_background_rate_is_never_an_incident` in tests/test_recurrence.py. |
