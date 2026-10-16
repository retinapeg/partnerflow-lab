# Reuse or build: decision record

Checked on 13 October 2026, before implementation. Star counts were read live from the
GitHub API on that date. Web search was used for existing "API fault-injection lab"
projects. A public search of X returned no posts for this topic, so X is effectively
unchecked.

## Decision

**Reuse the standard Python stack. Write the lab-specific glue by hand.**

No existing tool with 5,000 or more stars provides what this project is for: fault
scenarios switched by flags *inside* a FastAPI application, a recorded request log to
investigate, and a release-regression simulation, all runnable locally without Docker.
The glue is small and is the part worth being able to explain line by line.

## Reused

| Tool | Stars | Licence | Last push | Used for |
|---|---|---|---|---|
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | ~103,100 | MIT | 2026-10-08 | Routing, validation, `/docs` |
| [streamlit/streamlit](https://github.com/streamlit/streamlit) | ~46,000 | Apache-2.0 | 2026-10-13 | Dashboard |
| [pandas-dev/pandas](https://github.com/pandas-dev/pandas) | ~50,000 | BSD-3-Clause | 2026-10-12 | Tables for analytics |
| [encode/httpx](https://github.com/encode/httpx) | ~15,600 | BSD-3-Clause | 2026-10-02 | HTTP client: dashboard, traffic, tests |
| [scipy/scipy](https://github.com/scipy/scipy) | ~15,100 | BSD-3-Clause | 2026-10-12 | Normal distribution, chi-square |

Also used as specified in the brief: Uvicorn, Pydantic, NumPy, pytest, Altair (installed
with Streamlit), and Python's built-in `sqlite3`.

## Considered and not used

| Tool | Stars | Licence | Why not |
|---|---|---|---|
| [Shopify/toxiproxy](https://github.com/Shopify/toxiproxy) | ~12,400 | MIT | A Go network proxy. It can add latency or drop connections, but cannot produce application-level faults (auth failure, validation regression, release flags). Needs a separate binary. |
| [locustio/locust](https://github.com/locustio/locust) | ~28,200 | MIT | Load testing with its own web UI. The lab needs a small, seeded generator whose requests are identical run to run. That is about 60 lines with `random.Random(seed)`. |
| [fastapi/full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | ~46,000 | MIT | Requires Docker, PostgreSQL and React, all excluded by the brief. |
| [joke2k/faker](https://github.com/joke2k/faker) | ~19,400 | MIT | Only fictional business names were needed. Two short word lists do it with no dependency. |
| [statsmodels/statsmodels](https://github.com/statsmodels/statsmodels) | ~11,700 | BSD-3-Clause | Would compute the same intervals. The formulas are written out instead, using SciPy, so each step can be read and checked by hand. |
| [wiremock/wiremock](https://github.com/wiremock/wiremock), [mockoon/mockoon](https://github.com/mockoon/mockoon) | ~7,400 / ~8,400 | Apache-2.0 / MIT | Mock servers in Java and Node. They replace an API rather than extend this one. |
| [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | ~67,600 | BSD-3-Clause | Considered on 13 October 2026 for the support-ticket classifier. The brief asked for simple text classification first; keyword rules need no dependency and their accuracy is measured against the true causes. The natural next step if rules are not enough. |

## Below the 5,000-star threshold, so not shortlisted

| Tool | Stars | Note |
|---|---|---|
| [pydantic/httpx2](https://github.com/pydantic/httpx2) | ~1,500 | Starlette's test client now prefers it and prints a deprecation notice when it finds `httpx` instead. Created May 2026. The project stays on `httpx`; `pytest.ini` silences that one notice. Worth revisiting when it matures. |
| [laurentS/slowapi](https://github.com/laurentS/slowapi) | ~2,100 | Rate limiting. The lab's limiter is a 15-line fixed window. |
| [hynek/structlog](https://github.com/hynek/structlog) | ~5,000 (4,971) | Structured logging. The lab's log is a SQLite table, because it has to be queried. |
| [schemathesis/schemathesis](https://github.com/schemathesis/schemathesis) | ~3,700 | Property-based API testing. A good later addition. |
| [chaostoolkit/chaostoolkit](https://github.com/chaostoolkit/chaostoolkit) | ~2,000 | Infrastructure-level chaos experiments. |
