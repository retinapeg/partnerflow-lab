"""What to do when each kind of failure comes back.

A runbook is the agreed response to a known failure pattern. The triage agent
in partnerflow/triage.py never invents a diagnosis: it matches the evidence to
one of these entries and fills in the numbers. If a failure has no runbook, the
agent says so and asks a human.

Fields:
    class            short name for the kind of problem
    owner            who has to change something: "us", "partner" or "both"
    diagnosis        one sentence, in the words you would say to a colleague
    checks           what to verify before believing the diagnosis
    internal_action  what we do on our side
    auto_action      a safe, repeatable action the agent may take itself (or None)
    severity         default severity when the pattern is active
    partner_message  template for the message to the partner's engineers

Templates use {partner}, {count}, {n}, {share}, {endpoints}, {window},
{request_id}, {products} and {episodes}. They are filled by triage.py.
"""

RUNBOOKS = {
    "api_key_revoked": {
        "class": "authentication",
        "owner": "us",
        "diagnosis": "Every request from one partner fails with 401 from a point in time: "
                     "a key-lifecycle problem, not a code problem.",
        "checks": ["Did we rotate or disable the key, and when?",
                   "Has the partner deployed the new key to every instance?",
                   "Are other partners unaffected?"],
        "internal_action": "Confirm the revocation, issue or re-enable a key, and agree a "
                           "rotation calendar with the partner.",
        "auto_action": "open_incident",
        "severity": "high",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window} every call from your integration has returned 401 "
            "api_key_revoked ({count} of {n} requests to {endpoints}). This is a key "
            "state issue on our side, not a bug in your code. We are confirming when "
            "the key was disabled and will issue a replacement within the hour. If you "
            "rotated keys recently, please check that every server is using the new one: "
            "a stale key on one instance looks exactly like intermittent 401s.\n\n"
            "Example request ID for your logs: {request_id}."
        ),
    },
    "missing_api_key": {
        "class": "authentication",
        "owner": "partner",
        "diagnosis": "Requests arrive without an X-API-Key header, usually a proxy or "
                     "load balancer stripping it, or a new deployment missing the config.",
        "checks": ["Did the pattern start with a deployment on their side?",
                   "Does a request sent directly (curl) carry the header?"],
        "internal_action": "Reply with the exact header name and a working curl example.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window} we have received {count} requests from your integration with "
            "no X-API-Key header ({share} of your calls to {endpoints}). Nothing changed "
            "on our side. The usual cause is a proxy stripping the header or a new "
            "deployment without the key configured. A direct curl with the header will "
            "confirm which.\n\nExample request ID: {request_id}."
        ),
    },
    "invalid_api_key": {
        "class": "authentication",
        "owner": "partner",
        "diagnosis": "The key sent is not one we know: a typo, a sandbox key used against "
                     "live, or stray whitespace.",
        "checks": ["Is the key for this environment?",
                   "Does the pattern affect one instance or all of them?"],
        "internal_action": "Tell them which environment the key belongs to; never echo the key.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} requests to {endpoints} have used an API key "
            "we do not recognise. The most common cause is a sandbox key sent to the live "
            "API, or whitespace around the key. Please check the key in your configuration "
            "against the one in your partner dashboard.\n\nExample request ID: {request_id}."
        ),
    },
    "validation_error": {
        "class": "payload",
        "owner": "partner",
        "diagnosis": "A share of the partner's payloads do not fit the schema: a missing "
                     "field, wrong type or out-of-range value.",
        "checks": ["Which fields fail (the invalid_fields metadata)?",
                   "Did the share jump at a point in time (their release) or is it steady?",
                   "Did our schema change (our release)?"],
        "internal_action": "Send the field list with examples from /docs; if the share "
                           "jumped after our release, we changed the contract.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} applications ({share}) were rejected with 422 "
            "validation_error. The response body lists the fields; the most frequent ones "
            "are below. Our schema has not changed, so the likely cause is a change in how "
            "the payload is built. The interactive docs show a valid example for each "
            "field.\n\nExample request ID: {request_id}."
        ),
    },
    "rule_R101_product_limits": {
        "class": "business-rule",
        "owner": "partner",
        "diagnosis": "Valid JSON refused by an intended rule: the amount is outside the "
                     "product's limits.",
        "checks": ["Is the share in line with the partner's normal mix of amounts?",
                   "Do they show the limits to customers before submitting?"],
        "internal_action": "Nothing to fix. Point them at GET /partners for the limits.",
        "auto_action": None,
        "severity": "low",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "{count} of {n} applications since {window} were refused by rule R-101 "
            "(amount outside the product's limits). This is intended behaviour. The "
            "limits per product are available from GET /partners, so you can validate "
            "before submitting and avoid the round trip.\n\nExample request ID: {request_id}."
        ),
    },
    "rule_R204_amount_band": {
        "class": "release-regression",
        "owner": "us",
        "diagnosis": "Rule R-204 is refusing products it was never meant to apply to: the "
                     "v1.1.0 regression.",
        "checks": ["Which products are being refused? Only invoice_finance is intended.",
                   "Did the rate jump exactly at the release?",
                   "Are all partners affected (a release is never one partner)?"],
        "internal_action": "Apply the hotfix (v1.1.1) or roll back, then replay the refused "
                           "applications and tell every affected partner.",
        "auto_action": "open_incident",
        "severity": "high",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} applications ({share}) were refused with 422 "
            "rule_R204_amount_band. Products affected: {products}. This is a bug in our "
            "release v1.1.0: the rule was meant to apply to invoice finance only. You did "
            "nothing wrong. We are deploying the fix now and will re-submit the refused "
            "applications on your behalf, then confirm.\n\nExample request ID: {request_id}."
        ),
    },
    "rule_R204_intended": {
        "class": "business-rule",
        "owner": "partner",
        "diagnosis": "Rule R-204 refusing invoice finance in the manual-underwriting band, "
                     "as intended since v1.1.0.",
        "checks": ["Did the partner see the release notes?"],
        "internal_action": "Point them at the release notes and the manual route.",
        "auto_action": None,
        "severity": "low",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "{count} of {n} invoice-finance applications since {window} were refused by "
            "rule R-204 (amounts in the manual-underwriting band). This is the behaviour "
            "introduced in v1.1.0. Those customers should be routed to the manual "
            "underwriting path described in the release notes.\n\nExample request ID: {request_id}."
        ),
    },
    "customer_not_found": {
        "class": "identifiers",
        "owner": "partner",
        "diagnosis": "The partner submits customer IDs we do not have for them: a "
                     "sandbox/live mix-up or IDs stored wrongly.",
        "checks": ["Do the IDs look like another environment's?",
                   "Did their POST /customers calls succeed just before?"],
        "internal_action": "Send three example IDs with the environment they belong to.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} applications referenced a customer_id that does "
            "not exist for your account ({share}). A customer can only be used by the "
            "partner that created it, in the same environment. The usual cause is an ID "
            "from sandbox being sent to live, or the create step failing silently.\n\n"
            "Example request ID: {request_id}."
        ),
    },
    "application_not_found": {
        "class": "identifiers",
        "owner": "partner",
        "diagnosis": "Lookups for application IDs that do not belong to the partner.",
        "checks": ["Did the POST /applications that should have created it succeed?",
                   "Are they retrying a failed create and then fetching?"],
        "internal_action": "Trace the create call for one example ID.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} application lookups returned 404 ({share}). "
            "Each one referenced an ID that does not exist for your account. In most cases "
            "the create call before it had failed; please check its response before "
            "fetching.\n\nExample request ID: {request_id}."
        ),
    },
    "rate_limited": {
        "class": "rate-limit",
        "owner": "both",
        "diagnosis": "The partner exceeds its request limit in bursts, typically a "
                     "client retrying immediately after a timeout without backoff.",
        "checks": ["What is their request rate against the limit?",
                   "Do bursts follow a slow response from us?",
                   "Is the limit right for their volume?"],
        "internal_action": "Review their limit; share the backoff and idempotency guidance.",
        "auto_action": "attach_backoff_guidance",
        "severity": "medium",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} requests ({share}) were throttled with 429. Each "
            "response carries a Retry-After header. Retrying immediately turns one slow "
            "response into a storm, so please add exponential backoff with jitter and an "
            "idempotency key so retries cannot double-submit. We are also reviewing whether "
            "your limit matches your volume.\n\nExample request ID: {request_id}."
        ),
    },
    "server_exception": {
        "class": "server",
        "owner": "us",
        "diagnosis": "Our code failed. A 5xx is never the partner's fault.",
        "checks": ["Is it one endpoint and every partner (a dependency), or one partner?",
                   "Is the pattern fixed (every Nth) or random?",
                   "What does the stack trace for one request ID say?"],
        "internal_action": "Reproduce with a request ID, read the trace, add a timeout "
                           "and a circuit breaker so a dead dependency returns a clear "
                           "503 instead of a 500.",
        "auto_action": "page_oncall",
        "severity": "high",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} calls to {endpoints} returned 500 ({share}). "
            "This is a fault on our side; your integration did nothing wrong. We are "
            "investigating now and will update you within the hour, then share a short "
            "post-mortem.\n\nExample request ID: {request_id}."
        ),
    },
    "receiver_unavailable": {
        "class": "webhook",
        "owner": "both",
        "diagnosis": "Applications are accepted but the partner's webhook receiver "
                     "answers 503, so decisions never arrive.",
        "checks": ["What does their endpoint return right now?",
                   "How many deliveries are queued as failed?",
                   "Did it start with a deployment on their side?"],
        "internal_action": "Tell them their endpoint status, keep retrying with backoff, "
                           "replay once it is healthy.",
        "auto_action": "redeliver_webhooks",
        "severity": "high",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} of {n} decision webhooks could not be delivered: your "
            "endpoint returned 503. The applications themselves were accepted and decided. "
            "We will keep retrying and will replay every failed delivery as soon as your "
            "endpoint answers 200 again; nothing is lost."
        ),
    },
    "route_not_found": {
        "class": "routing",
        "owner": "partner",
        "diagnosis": "The URL matches no endpoint: a typo or a wrong base URL.",
        "checks": ["What path are they calling?"],
        "internal_action": "Reply with the correct path from /docs.",
        "auto_action": None,
        "severity": "low",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} requests hit a URL that does not exist on our API. "
            "Please compare the path against the interactive docs.\n\n"
            "Example request ID: {request_id}."
        ),
    },
    "method_not_allowed": {
        "class": "routing",
        "owner": "partner",
        "diagnosis": "Right URL, wrong HTTP method.",
        "checks": ["GET where POST was meant, or the reverse?"],
        "internal_action": "Reply with the method per endpoint.",
        "auto_action": None,
        "severity": "low",
        "partner_message": (
            "Hi {partner} team,\n\n"
            "Since {window}, {count} requests used an HTTP method the endpoint does not "
            "accept (405). Please check the method against the docs.\n\n"
            "Example request ID: {request_id}."
        ),
    },
}

FALLBACK_BY_STATUS_CLASS = {
    4: {
        "class": "client-error",
        "owner": "partner",
        "diagnosis": "A 4xx without a catalogued reason: the caller needs to change something.",
        "checks": ["Read the response body for one example."],
        "internal_action": "Catalogue the reason and write a runbook.",
        "auto_action": None,
        "severity": "medium",
        "partner_message": "Hi {partner} team,\n\nSince {window}, {count} of {n} requests "
                           "failed with a client error. Example request ID: {request_id}.",
    },
    5: {
        "class": "server",
        "owner": "us",
        "diagnosis": "A 5xx without a catalogued reason. It is ours.",
        "checks": ["Read the server log for one request ID."],
        "internal_action": "Investigate and catalogue.",
        "auto_action": "page_oncall",
        "severity": "high",
        "partner_message": "Hi {partner} team,\n\nSince {window}, {count} of {n} requests "
                           "failed on our side. We are investigating. Example request ID: {request_id}.",
    },
}

# The only actions the agent is allowed to take by itself. Everything else is a
# recommendation for a person. Nothing here changes a fault switch or a release.
AUTO_ACTIONS = {
    "redeliver_webhooks": "Retry every failed webhook delivery (POST /lab/webhooks/redeliver).",
    "open_incident": "Open an incident record so the pattern is tracked to resolution (simulated).",
    "page_oncall": "Page the on-call engineer for a server-side fault (simulated).",
    "attach_backoff_guidance": "Attach the backoff and idempotency guidance to the partner message.",
}


def lookup(status_code: int, failure_reason: str | None) -> dict:
    """The runbook for a failure, or the status-class fallback."""
    if failure_reason in RUNBOOKS:
        return RUNBOOKS[failure_reason]
    return FALLBACK_BY_STATUS_CLASS.get(status_code // 100, FALLBACK_BY_STATUS_CLASS[5])
