"""API playground: send one request and read exactly what came back."""

import json

import streamlit as st

from partnerflow import config, explain
from views import common

CALLERS = {
    "Alder Marketplace (valid key)": "demo-key-alder-0001",
    "Birch Books (valid key)": "demo-key-birch-0002",
    "Cedar Pay (valid key)": "demo-key-cedar-0003",
    "Nobody (no API key)": None,
    "Somebody with a wrong API key": "this-key-does-not-exist",
}

REQUESTS = ["POST /customers", "POST /applications", "GET /customers/{id}",
            "GET /applications/{id}", "GET /partners", "GET /health", "GET /metrics"]

CUSTOMER_EXAMPLES = {
    "Valid customer": {"business_name": "Harbour Street Bakery Ltd",
                       "contact_email": "owner@harbourstreetbakery.example",
                       "annual_turnover": 400000, "trading_months": 36},
    "Invalid: email is not an email": {"business_name": "Harbour Street Bakery Ltd",
                                       "contact_email": "not-an-email",
                                       "annual_turnover": 400000, "trading_months": 36},
    "Invalid: misspelt field name": {"business_name": "Harbour Street Bakery Ltd",
                                     "contact_email": "owner@harbourstreetbakery.example",
                                     "anual_turnover": 400000, "trading_months": 36},
}


def application_examples(customer_id: int) -> dict:
    valid = {"customer_id": customer_id, "product": "term_loan", "amount": 20000, "term_months": 12}
    return {
        "Valid application": valid,
        "Valid, amount 30,000 (inside the R-204 band)": {**valid, "amount": 30000},
        "Invalid: amount is missing": {k: v for k, v in valid.items() if k != "amount"},
        "Invalid: amount is text": {**valid, "amount": "twenty thousand"},
        "Invalid: negative amount": {**valid, "amount": -20000},
        "Invalid: unknown product": {**valid, "product": "overdraft"},
        "Refused by rule R-101: over the product limit": {**valid, "product": "credit_line",
                                                          "amount": 150000},
        "Customer does not exist": {**valid, "customer_id": 999999},
    }


def render(lab: dict) -> None:
    st.title("API playground")
    st.write("Build a request, send it to the real API, and see the status code, the response "
             "and what the API wrote in its log. Try breaking it: the errors are the lesson.")

    left, right = st.columns(2)
    caller = left.selectbox("Who is calling?", list(CALLERS))
    request = right.selectbox("Request", REQUESTS)
    method, path = request.split(" ")
    api_key = CALLERS[caller]
    headers = {"X-API-Key": api_key} if api_key else {}
    last_customer = st.session_state.get("last_customer_id", {}).get(api_key)

    body_text = None
    if "{id}" in path:
        default_id = str(last_customer or 1)
        resource_id = st.text_input("ID to fetch", value=default_id,
                                    help="Try an ID that does not exist, or letters instead of a number.")
        path = path.replace("{id}", resource_id.strip() or "1")
    elif method == "POST":
        if path == "/customers":
            examples = CUSTOMER_EXAMPLES
        else:
            examples = application_examples(last_customer or 1)
            if last_customer is None:
                st.info("This caller has not created a customer in this session yet. Send "
                        "**POST /customers** first, or the application will get a 404.")
        example = st.selectbox("Start from an example", list(examples))
        body_text = st.text_area(
            "JSON body (edit it freely)", value=json.dumps(examples[example], indent=2),
            height=190, key=f"body_{path}_{example}_{last_customer}")

    if st.button("Send request", type="primary"):
        send(method, path, headers, body_text, api_key)

    result = st.session_state.get("playground_result")
    if result:
        show_result(result)


def send(method: str, path: str, headers: dict, body_text: str | None, api_key: str | None) -> None:
    kwargs = {"headers": headers}
    if body_text is not None:
        # Sent as raw text, so even broken JSON reaches the API unchanged.
        kwargs["content"] = body_text
        kwargs["headers"] = {**headers, "Content-Type": "application/json"}
    response = common.call_api(method, path, **kwargs)

    try:
        body = response.json()
    except ValueError:
        body = response.text
    if method == "POST" and path == "/customers" and response.status_code == 201:
        st.session_state.setdefault("last_customer_id", {})[api_key] = body["id"]

    st.session_state["playground_result"] = {
        "request": f"{method} {path}",
        "status_code": response.status_code,
        "latency_ms": response.elapsed.total_seconds() * 1000,
        "request_id": response.headers.get("X-Request-ID"),
        "retry_after": response.headers.get("Retry-After"),
        "body": body,
        "curl": common.curl_command(method, path, headers, body_text),
    }


def show_result(result: dict) -> None:
    st.divider()
    st.subheader(f"Response to {result['request']}")
    status_code = result["status_code"]
    body = result["body"]
    failure_reason = body["error"]["code"] if isinstance(body, dict) and "error" in body else None
    guide = explain.explain(status_code, failure_reason)

    cols = st.columns(3)
    cols[0].metric("Status code", status_code, border=True)
    cols[1].metric("Round trip", f"{result['latency_ms']:.0f} ms", border=True)
    with cols[2].container(border=True):
        st.caption("X-Request-ID (use it to find this request in the log)")
        st.code(result["request_id"] or "-", language="text")

    if status_code < 400:
        st.success(guide["meaning"])
    else:
        st.error(f"**{failure_reason or status_code}**: {guide['meaning']}")
        st.write(f"**Where it came from:** {guide['origin']}")
        st.write(f"**What to check next:** {guide['next_step']}")
    if result["retry_after"]:
        st.write(f"**Retry-After header:** {result['retry_after']} seconds")

    left, right = st.columns(2)
    with left:
        st.write("**Response body**")
        if isinstance(body, (dict, list)):
            st.json(body)
        else:
            st.code(body or "(empty)", language="text")
    with right:
        st.write("**The same request as a curl command**")
        st.code(result["curl"], language="bash", wrap_lines=True)
        if result["request_id"] and not result["request"].split(" ")[1].startswith(config.UNLOGGED_PREFIXES):
            st.write("**What the API recorded**")
            entry = common.call_api("GET", f"/lab/requests/{result['request_id']}")
            if entry.status_code == 200:
                st.json(entry.json(), expanded=False)
        else:
            st.caption("Health, metrics and docs calls are not written to the request log.")
