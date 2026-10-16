"""Plain-English guide to every failure reason the API can record.

Used by the dashboard's investigation page. Each entry says what the status
means, which piece of code produced it, and what to check next.
"""

FAILURE_GUIDE = {
    "missing_api_key": {
        "status": 401,
        "meaning": "The request had no X-API-Key header.",
        "origin": "require_partner() in main.py. Runs before the body is validated.",
        "next_step": "Check the partner is sending the header at all (a proxy may strip it).",
    },
    "invalid_api_key": {
        "status": 401,
        "meaning": "The key sent is not one this API knows.",
        "origin": "require_partner() in main.py.",
        "next_step": "Check for a typo, a key for another environment, or stray whitespace.",
    },
    "api_key_revoked": {
        "status": 401,
        "meaning": "The key is known but has been switched off.",
        "origin": "require_partner() in main.py, via the auth_revoked fault.",
        "next_step": "Confirm when the key was revoked and issue the partner a new one.",
    },
    "validation_error": {
        "status": 422,
        "meaning": "The JSON did not match the schema: a missing field, wrong type or out-of-range value.",
        "origin": "Pydantic, inside FastAPI, before the endpoint function runs. "
                  "See schemas.py for the rules.",
        "next_step": "Read the 'invalid_fields' in the metadata and compare the partner's payload with /docs.",
    },
    "rule_R101_product_limits": {
        "status": 422,
        "meaning": "The JSON was valid, but the amount is outside the product's limits.",
        "origin": "check_product_limits() in rules.py, called by create_application(). An intended rule.",
        "next_step": "Nothing to fix on our side. Point the partner at GET /partners for the limits.",
    },
    "rule_R204_amount_band": {
        "status": 422,
        "meaning": "The JSON was valid, but rule R-204 refused the amount band.",
        "origin": "check_r204_amount_band() in rules.py. Only exists in release B.",
        "next_step": "Check the product. R-204 is intended for invoice_finance only. "
                     "If other products are hit, that is the v1.1.0 bug.",
    },
    "customer_not_found": {
        "status": 404,
        "meaning": "No customer with that ID belongs to the calling partner.",
        "origin": "The endpoint function in main.py, after looking in SQLite.",
        "next_step": "Check the ID came from this environment and from this partner's own POST /customers.",
    },
    "application_not_found": {
        "status": 404,
        "meaning": "No application with that ID belongs to the calling partner.",
        "origin": "get_application() in main.py, after looking in SQLite.",
        "next_step": "Check whether the POST /applications that should have created it succeeded.",
    },
    "route_not_found": {
        "status": 404,
        "meaning": "The URL does not match any endpoint.",
        "origin": "FastAPI's router. No endpoint function ran.",
        "next_step": "Check the path for typos or a wrong base URL.",
    },
    "method_not_allowed": {
        "status": 405,
        "meaning": "The URL exists but not for this HTTP method.",
        "origin": "FastAPI's router. No endpoint function ran.",
        "next_step": "Check the method (GET vs POST) against /docs.",
    },
    "rate_limited": {
        "status": 429,
        "meaning": "The partner sent more requests than allowed in the time window.",
        "origin": "rate_limit_retry_after() in faults.py, called by require_partner().",
        "next_step": "Ask the partner to honour the Retry-After header and retry with backoff.",
    },
    "server_exception": {
        "status": 500,
        "meaning": "Our code raised an exception nobody handled. This is our fault, not the caller's.",
        "origin": "Caught by record_request() in main.py. The metadata names the exception and where it was raised.",
        "next_step": "Search the server console for the request_id to read the full stack trace.",
    },
}

STATUS_CLASSES = {
    2: "Success. The API did what was asked.",
    4: "Client error. The request was refused; the caller needs to change something.",
    5: "Server error. The API failed; the caller did nothing wrong.",
}


def explain(status_code: int, failure_reason: str | None) -> dict:
    """Return the guide entry for a recorded request."""
    if failure_reason in FAILURE_GUIDE:
        return FAILURE_GUIDE[failure_reason]
    return {
        "status": status_code,
        "meaning": STATUS_CLASSES.get(status_code // 100, "Unusual status code."),
        "origin": "The endpoint function in main.py." if status_code < 400 else "Not catalogued.",
        "next_step": "Nothing to do." if status_code < 400 else "Read the response body.",
    }
