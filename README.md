# PartnerFlow Lab

> **Synthetic data. Fictional lending simulation. Not affiliated with iwoca.**

A small lending API that runs on your laptop, which you can break on purpose and then
investigate. Three pretend partners send it loan applications. A dashboard lets you send
requests, switch failures on, replay a faulty release, and analyse the results.

Nothing here is real: no real lender, customers, money or credit decisions.

Two halves, one lab:

- **Operations** (this folder): the API partners integrate with: keys, faults, a faulty release
  and the monitoring that catches it. Three-minute walkthrough: [DEMO_SCRIPT.md](DEMO_SCRIPT.md).
- **Experiments** ([`experiments/`](experiments/)): a simulated partner funnel with the true
  effects baked in, and the estimators tested against them. When pre-qualification rolls out
  partner by partner and the biggest partners go first, naive before/after overstates the lift
  by about four points; difference-in-differences lands within half a point; a hierarchical
  Bayesian model's partner-level intervals cover 92.5% of true rates. The results table in
  [`experiments/README.md`](experiments/README.md) is generated from the passing tests.

## Run it

```bash
./start.sh
```

The first run creates `.venv` and installs the packages (needs Python 3.10 or newer, and
internet once). After that everything runs offline, on this machine only.

| What | Address |
|---|---|
| Dashboard | http://127.0.0.1:8510 |
| API docs (interactive) | http://127.0.0.1:8010/docs |
| API | http://127.0.0.1:8010 |

| To do this | Run this |
|---|---|
| Stop both | `Ctrl+C` in the terminal running `start.sh` |
| Wipe recorded data | `.venv/bin/python reset.py` (or "Reset the lab" in the dashboard sidebar) |
| Run the tests | `.venv/bin/python -m pytest` |
| Replay the release story in the terminal | `.venv/bin/python demo.py` |

Ports 8010 and 8510 are used so the lab never clashes with a plain `uvicorn main:app`
on port 8000. The three-minute walkthrough is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

Demo API keys (fictional, one per partner): `demo-key-alder-0001`, `demo-key-birch-0002`,
`demo-key-cedar-0003`.

---

## How it works

### What FastAPI does

FastAPI is a Python library for writing web APIs. You write an ordinary function and put
a decorator on it saying which URL and HTTP method it answers:

```python
@app.get("/health")
def health():
    return {"status": "ok"}
```

FastAPI then does four jobs for you:

1. **Routing.** It matches an incoming method and URL to the right function.
2. **Validation.** It converts incoming JSON into Python objects and checks them.
3. **Serialisation.** It turns whatever your function returns into JSON.
4. **Documentation.** It builds the `/docs` page from your code.

FastAPI does not listen on the network. That is Uvicorn's job.

### What Uvicorn does

Uvicorn is the server. It opens a network port, waits for HTTP requests, reads the raw
bytes, passes each request to the FastAPI app, and sends the app's answer back.

`uvicorn main:app --port 8010` means: import `main.py`, find the object named `app`,
and serve it on port 8010. FastAPI decides *what* to answer. Uvicorn does the *talking*.

### What happens during an HTTP request

Follow one `POST /applications` through [main.py](main.py):

1. The client sends a method (`POST`), a path (`/applications`), headers (`X-API-Key`,
   `Content-Type`) and a JSON body.
2. Uvicorn receives it and calls the app.
3. `record_request()` runs first. It is *middleware*: code wrapped around every request.
   It creates a request ID, reads the lab settings from SQLite and starts a timer.
4. FastAPI finds the function for this method and path: `create_application()`.
5. `require_partner()` runs. It checks the API key (401 if missing or unknown). The
   rate-limit and latency faults also act here.
6. Pydantic checks the JSON body against `ApplicationCreate` (422 if it does not fit).
7. `create_application()` runs. It looks up the customer in SQLite (404 if absent),
   applies the business rules (422 if refused), calls the simulated scoring service
   (500 if that crashes), stores the application and returns it.
8. FastAPI turns the returned value into JSON with status 201.
9. Back in `record_request()`: it stops the timer, adds the `X-Request-ID` header and
   writes one row to the `request_log` table.
10. Uvicorn sends the response to the client.

Steps 5 to 7 run in that order, so a request with a bad key *and* a bad body gets 401,
not 422.

### How Pydantic validates incoming JSON

[partnerflow/schemas.py](partnerflow/schemas.py) describes each JSON body as a class:

```python
class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: int = Field(gt=0)
    product: Literal["term_loan", "credit_line", "invoice_finance"]
    amount: int = Field(gt=0, le=10_000_000)
    term_months: int = Field(ge=1, le=60)
    purpose: str | None = Field(default=None, max_length=200)
```

Because `create_application(body: ApplicationCreate, ...)` names this class as the type
of its `body` argument, FastAPI hands the JSON to Pydantic before the function runs.
Pydantic checks:

- every required field is present (`purpose` has a default, so it is optional)
- each value has the right type (`amount` must be a whole number)
- each value is in range (`gt=0` means greater than zero)
- `product` is one of the three allowed words
- there are no unknown fields (`extra="forbid"`), so a misspelt field name is an error

If anything fails, the endpoint function never runs. The caller gets a 422 listing every
problem at once. Two details worth knowing: `"20000"` (a string of digits) is accepted
and converted to the number 20000, while `1500.5` is rejected because it is not whole.

### How SQLite stores applications

SQLite is a database kept in one file: `data/partnerflow.db`. There is no database server
to install, and Python can talk to it with its built-in `sqlite3` module.

[partnerflow/db.py](partnerflow/db.py) creates six tables: `partners`, `customers`,
`applications`, `request_log`, `webhook_deliveries` and `lab_config`. Storing an
application is one SQL statement:

```python
conn.execute(
    "INSERT INTO applications (customer_id, partner_id, product, amount, ...) "
    "VALUES (?, ?, ?, ?, ...)",
    (data["customer_id"], partner_id, data["product"], data["amount"], ...),
)
```

The `?` marks are placeholders. Values are passed separately and never glued into the
SQL text, which is what prevents SQL injection. Each function opens a connection, runs
its statement, commits and closes. Because it is a file, the data survives a restart.
You can look at it directly:

```bash
sqlite3 data/partnerflow.db "SELECT id, product, amount, status FROM applications LIMIT 5;"
```

### How the dashboard queries data

The dashboard ([dashboard.py](dashboard.py) and the [views/](views/) folder) is a
separate program. It gets data in two ways, both in [views/common.py](views/common.py):

1. **Over HTTP, like any partner.** Every action (sending a request, switching a release,
   toggling a fault, reading `/metrics`) is a real HTTP call made with `httpx`. So when
   the dashboard switches a fault on, the API's behaviour changes for every caller.
2. **Reading the SQLite file directly, read-only.** For analysis the dashboard needs
   whole tables at once, so `analytics.load_requests()` runs
   `SELECT * FROM request_log` with pandas. The file is opened with `mode=ro`, so this
   path cannot change anything.

Streamlit re-runs the dashboard script from top to bottom on every click, so each page
always shows the current state of the API and the database.

### Where different HTTP errors originate

| Status | Code in the response | Raised by | When |
|---|---|---|---|
| 401 | `missing_api_key`, `invalid_api_key` | `require_partner()` in main.py | No key, or a key the API does not know |
| 401 | `api_key_revoked` | `require_partner()` | Fault A is on for that partner |
| 404 | `route_not_found` | FastAPI's router | The URL matches no endpoint |
| 404 | `customer_not_found`, `application_not_found` | The endpoint function, after a SQLite lookup | The ID does not exist, or belongs to another partner |
| 405 | `method_not_allowed` | FastAPI's router | Right URL, wrong method |
| 422 | `validation_error` | Pydantic, before the endpoint runs | The JSON does not fit the schema |
| 422 | `rule_R101_product_limits` | `check_product_limits()` in rules.py | Valid JSON, amount outside the product's limits |
| 422 | `rule_R204_amount_band` | `check_r204_amount_band()` in rules.py | Valid JSON, refused by the release B rule |
| 429 | `rate_limited` | `rate_limit_retry_after()` in faults.py | Fault F is on and the partner is over its limit |
| 500 | `server_exception` | Caught by `record_request()` in main.py | Code raised an exception that nothing handled |

The rule of thumb: **4xx means the caller must change something. 5xx means the API
failed and the caller did nothing wrong.** A 422 has two very different sources here:
Pydantic (the JSON is malformed) or a business rule (the JSON is fine, the request is
refused). The `code` tells you which.

### How to investigate a failed request

1. **Get the request ID.** It is in the `X-Request-ID` response header and in every
   error body.
2. **Look it up.** `curl http://127.0.0.1:8010/lab/requests/<id>`, or paste it into the
   dashboard's *Incident investigation* page.
3. **Read the status class.** 4xx: look at what the caller sent. 5xx: look at our code.
4. **Read `failure_reason` and `metadata`.** The table above says which code produced
   that reason. The metadata holds the non-sensitive facts: product, amount, rule ID,
   invalid field names, exception type.
5. **For a 500, search the server console for the request ID.** The full stack trace is
   printed there, next to `Unhandled exception, request_id=...`.
6. **Ask whether it is one request or a pattern.** Group failures by partner, product,
   amount band and release (sections 2 and 3 of the investigation page).
7. **Compare with a time it worked.** The same request in an earlier run or release is
   the fastest way to see what changed.

The log never stores API keys, email addresses or the values of invalid fields.

---

## Try it with curl

Start the lab, then run these in a second terminal. The output shown is real output
from this project (your timestamps and request IDs will differ).

**A healthy API**

```bash
curl http://127.0.0.1:8010/health
```
```json
{"status":"ok","release_version":"v1.0.0","database":"ok"}
```

**Create a customer** (`-i` also prints the status line and headers)

```bash
curl -i -X POST http://127.0.0.1:8010/customers \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"business_name": "Harbour Street Bakery Ltd", "contact_email": "owner@harbourstreetbakery.example", "annual_turnover": 400000, "trading_months": 36}'
```
```
HTTP/1.1 201 Created
content-type: application/json
x-request-id: 2d0ceefaaca7495b

{"id":1,"partner_id":"alder","business_name":"Harbour Street Bakery Ltd","contact_email":"owner@harbourstreetbakery.example","annual_turnover":400000,"trading_months":36,"created_at":"2026-10-13T01:36:47.756+00:00"}
```

**Submit an application, then fetch it**

```bash
curl -X POST http://127.0.0.1:8010/applications \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "term_loan", "amount": 20000, "term_months": 12}'
```
```json
{"id":1,"customer_id":1,"partner_id":"alder","product":"term_loan","amount":20000,"term_months":12,"purpose":null,"status":"approved","decision_reason":null,"release_version":"v1.0.0","created_at":"2026-10-13T01:36:47.784+00:00"}
```
```bash
curl http://127.0.0.1:8010/applications/1 -H "X-API-Key: demo-key-alder-0001"
```

**Four ways to fail**

```bash
# 401: no API key
curl -X POST http://127.0.0.1:8010/applications \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "term_loan", "amount": 20000, "term_months": 12}'
```
```json
{"error":{"code":"missing_api_key","message":"Send your partner key in the X-API-Key header.","request_id":"2a396298fdbc4bbb","details":null}}
```
```bash
# 422 from Pydantic: three things wrong with the JSON, all reported together
curl -X POST http://127.0.0.1:8010/applications \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "overdraft", "amount": -5}'
```
```json
{"error":{"code":"validation_error","message":"The request did not match the expected format.","request_id":"6db7ec20eb2149c3","details":[{"field":"product","problem":"Input should be 'term_loan', 'credit_line' or 'invoice_finance'","type":"literal_error"},{"field":"amount","problem":"Input should be greater than 0","type":"greater_than"},{"field":"term_months","problem":"Field required","type":"missing"}]}}
```
```bash
# 404: no such application
curl http://127.0.0.1:8010/applications/999 -H "X-API-Key: demo-key-alder-0001"
```
```json
{"error":{"code":"application_not_found","message":"No application 999 for this partner.","request_id":"2db345e398414b79","details":null}}
```
```bash
# 422 from a business rule: the JSON is valid, the amount is over the product limit
curl -X POST http://127.0.0.1:8010/applications \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "credit_line", "amount": 150000, "term_months": 12}'
```
```json
{"error":{"code":"rule_R101_product_limits","message":"credit_line accepts amounts from 1,000 to 100,000 GBP. Requested: 150,000.","request_id":"fb497f29b9c942ab","details":[{"rule_id":"R-101"}]}}
```

**Reproduce the release bug, then fix it**

```bash
# Deploy the faulty release
curl -X POST http://127.0.0.1:8010/lab/release \
  -H "Content-Type: application/json" -d '{"release": "B"}'

# A 30,000 term loan is now refused. It should not be.
curl -i -X POST http://127.0.0.1:8010/applications \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "term_loan", "amount": 30000, "term_months": 12}'
```
```
HTTP/1.1 422 Unprocessable Entity
x-request-id: 93d739ea57e5409c

{"error":{"code":"rule_R204_amount_band","message":"Applications between 25,000 and 50,000 GBP need manual underwriting and cannot be submitted through the API.","request_id":"93d739ea57e5409c","details":[{"rule_id":"R-204"}]}}
```
```bash
# Inspect what the API recorded (use the request ID you got back)
curl http://127.0.0.1:8010/lab/requests/93d739ea57e5409c
```
```json
{"id":10,"timestamp":"2026-10-13T01:36:47.880+00:00","request_id":"93d739ea57e5409c","partner_id":"alder","method":"POST","endpoint":"/applications","path":"/applications","status_code":422,"latency_ms":1.5,"failure_reason":"rule_R204_amount_band","release_version":"v1.1.0","run_label":null,"metadata":{"customer_id":1,"product":"term_loan","amount":30000,"term_months":12,"rule_id":"R-204"}}
```
```bash
# Apply the targeted fix (patch v1.1.1)
curl -X POST http://127.0.0.1:8010/lab/hotfix \
  -H "Content-Type: application/json" -d '{"enabled": true}'
```
Send the same 30,000 `term_loan` again and it returns **201**. Send it as
`invoice_finance` and it still returns **422**: the rule the release was meant to add is
still enforced.

**Cause a server error, then switch it off**

```bash
curl -X PUT http://127.0.0.1:8010/lab/faults/server_error \
  -H "Content-Type: application/json" -d '{"enabled": true, "every_nth": 1}'

curl -X POST http://127.0.0.1:8010/applications \
  -H "X-API-Key: demo-key-alder-0001" \
  -H "Content-Type: application/json" \
  -d '{"customer_id": 1, "product": "term_loan", "amount": 20000, "term_months": 12}'
```
```json
{"error":{"code":"server_exception","message":"The server hit an unexpected error. Quote the request_id.","request_id":"a52535ee2d2b433f","details":null}}
```
The terminal running the lab now shows `Unhandled exception, request_id=a52535ee2d2b433f`
followed by the stack trace, ending in `ScoringServiceError`.

```bash
curl -X PUT http://127.0.0.1:8010/lab/faults/server_error \
  -H "Content-Type: application/json" -d '{"enabled": false}'
```

**Metrics, recent failures, reset**

```bash
curl http://127.0.0.1:8010/metrics
curl "http://127.0.0.1:8010/lab/requests?failures_only=true&limit=5"
curl -X POST http://127.0.0.1:8010/lab/reset
```

---

## The release story

| Lab setting | Version | What rule R-204 does |
|---|---|---|
| Release A | v1.0.0 | Does not exist |
| Release B | v1.1.0 | **Bug:** refuses every product in the 25,000 to 50,000 band |
| Release B + hotfix | v1.1.1 | Refuses `invoice_finance` only in that band, as intended |

The release notes for v1.1.0 say R-204 applies to `invoice_finance` only. The code in
[partnerflow/rules.py](partnerflow/rules.py) forgets to check the product. The fix
restores that check. It does not remove the rule, and it does not touch R-101 (the
product limits), which is enforced in every release.

The dashboard's *Release simulation* page sends the same seeded traffic before the
release, after it, and after the fix. With the defaults (150 journeys, seed 7) the
refusal rate for `POST /applications` goes 4.0% → 27.3% → 10.7%. It does not return to
4.0%, and should not: the difference is the intended `invoice_finance` rule.

## Fault scenarios

| | Scenario | Side | Result |
|---|---|---|---|
| A | Authentication failure | server | 401 `api_key_revoked` for one partner |
| B | Invalid application payloads | client | 422 `validation_error` |
| C | Missing customer or application | client | 404 |
| D | Server-side exception | server | 500 on every Nth application |
| E | Increased latency | server | Same status codes, slower |
| F | Rate limiting | server | 429 with `Retry-After` |
| G | Validation regression after a release | server | The release story above |
| H | Webhook delivery failure | server | Request succeeds, delivery recorded as failed |

*Server* faults change what the API does. *Client* faults make the traffic generator
send genuinely bad requests, so the API reacts as it would to a partner's mistake. No
fault uses randomness, so the same traffic always gives the same result.

## Data science

[partnerflow/synthetic.py](partnerflow/synthetic.py) generates a reproducible dataset of
fictional customer journeys (default 12,000 rows, seed 42). It is a simulator with known
ground truth, kept separate from the live request log.
[partnerflow/analytics.py](partnerflow/analytics.py) computes:

- conversion funnels, overall and per partner
- partner comparison with confidence intervals
- API error rates, changes between releases, and segments that fail disproportionately
  (these run on the **live request log**)
- the link between API errors and drop-off, as a naive figure, an adjusted figure, and
  the simulator's true effect, to show correlation is not causation
- a randomised A/B test: control and treatment conversion, absolute effect, 95%
  confidence interval, sample size and p-value

With the defaults: journeys with an API error complete 18.0 points less often, but the
true causal effect built into the simulator is 12.2 points. The gap is confounding by
application size. The A/B test estimates +5.5 points (95% interval +3.9 to +7.2)
against a true lift of +4.0.

## Broker monitoring (statistics at volume)

The *Broker monitoring* page is a separate simulated estate: five fictional brokers sending
about a million requests over a fortnight, generated in under a second by
[partnerflow/brokersim.py](partnerflow/brokersim.py). Nothing is stored per request. Each
broker-hour is one row of counts and latency summaries (1,680 rows for ~1,000,000 requests);
latencies are drawn per request in one vectorised pass, summarised, and discarded.
Applications (~50,000) and support tickets (~2,000) are generated per item because the
questions asked of them need individuals.

[partnerflow/monitoring.py](partnerflow/monitoring.py) does the statistics:

- **Expected vs observed failure rate** per broker, with Wilson intervals, the binomial
  variance `p(1-p)/n` and its square root, and a *dispersion* ratio (observed variance of
  the hourly rates over the variance binomial noise predicts; about 1 when stable).
- **Control chart** per broker: hourly rate, expected rate, `±3σ` limits that widen when
  traffic is thin. An hour is flagged on the exact binomial tail probability
  `P(X ≥ x)` rather than the z-score, because the normal approximation over-flags
  small counts (30 requests, 3 failures at 2% expected is z = 3.1 but a 2% tail).
- **Multiple comparisons**: 1,680 broker-hours tested at α = 0.00135 gives ~2.3 false
  flags by chance; runs of consecutive flags are what to trust.
- **Incident grouping** with the evidence an integration manager needs: excess failures,
  peak z, tail probability, error-type mix vs baseline, p95 latency vs baseline, whether
  other brokers moved, affected customers, tickets raised.
- **Lending outcomes**: cross-broker correlation (confounded), before-vs-during for the
  affected broker, and difference-in-differences against unaffected brokers, shown beside
  the simulator's true effect. Exposure of an application to failures is `1 − (1 − p)^k`
  for k API calls.
- **Tickets**: keyword classification into authentication / timeout / validation /
  webhook / other, with accuracy, precision, recall and a confusion matrix against the
  true causes.

Built-in incident: Meridian Capital's timeouts spike from 09:00 to 16:59 on day 10. You can
add faults of your own (broker, time, duration, failure rate, error type, latency).

## Project layout

```
main.py                 The API: endpoints, middleware, error handling
partnerflow/
  config.py             Partners, demo keys, products, release notes, fault defaults
  schemas.py            Pydantic models (request and response shapes)
  db.py                 SQLite tables and queries
  rules.py              R-101, R-204 (with the bug and the fix), toy decision
  faults.py             Server-side fault injection
  metrics.py            Numbers behind GET /metrics
  traffic.py            Seeded traffic generator (real HTTP calls)
  synthetic.py          Synthetic journeys dataset
  analytics.py          Statistics and analysis functions
  brokersim.py          Five-broker ~1M-request simulator (hourly aggregates, applications, tickets)
  monitoring.py         Control charts, incident detection, difference-in-differences
  explain.py            Plain-English guide to each failure reason
  charts.py             Chart helpers for the dashboard
dashboard.py, views/    The Streamlit dashboard, one file per page
run.py, start.sh        One-command launcher
reset.py, demo.py       Reset, and the release story from the terminal
tests/                  151 automated tests
```

## Known limitations

- **Local learning tool, not production code.** One server process, no HTTPS, no real
  authentication. The API keys are fixed demo strings compared in plain text. A real
  system stores hashed keys and compares them in constant time.
- **`/lab/...`, `/metrics` and `/health` need no key.** Fine on localhost; never on a
  network.
- **The `/docs` page needs internet to draw itself.** FastAPI's default docs page loads
  the Swagger UI script and stylesheet from a public CDN (cdn.jsdelivr.net). The API,
  the dashboard, the demo and the raw schema at `/openapi.json` all work offline.
- **`/metrics?run_label=...` filters the request numbers only.** Its `applications`
  block always counts every stored application, because applications are not tagged
  with a run.
- **Fault counters and rate-limit windows live in memory**, so they reset when the API
  restarts. Data and settings are in SQLite and do survive.
- **The request log is written inside the request**, which adds a millisecond or two.
  Real systems log asynchronously or to a separate service.
- **Webhooks are simulated in-process.** No HTTP call leaves the API, and retries
  happen immediately rather than with backoff.
- **The decision rule is a toy.** It is three `if` statements, not a credit model.
- **The synthetic dataset is a simulation.** Its effect sizes are invented. It
  demonstrates methods, not findings about any real business.
- **Statistics are the simple textbook versions**: Wilson and Wald intervals, a
  two-proportion z-test, a chi-square test. No correction for peeking or for multiple
  comparisons beyond a strict threshold.
- **Money is whole pounds.** No pence, no currencies.

Why existing tools were reused or not is recorded in [REUSE_DECISION.md](REUSE_DECISION.md).
