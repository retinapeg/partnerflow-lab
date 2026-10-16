"""Small helpers used by several test files."""

CUSTOMER = {
    "business_name": "Harbour Street Bakery Ltd",
    "contact_email": "owner@harbourstreetbakery.example",
    "annual_turnover": 400_000,
    "trading_months": 36,
}


def new_customer(client, headers, **changes) -> dict:
    response = client.post("/customers", json={**CUSTOMER, **changes}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def apply(client, headers, customer_id, product="term_loan", amount=20_000, term_months=12):
    """POST /applications and return the raw response (it may be an error)."""
    body = {"customer_id": customer_id, "product": product, "amount": amount,
            "term_months": term_months}
    return client.post("/applications", json=body, headers=headers)


def log_entry(client, response) -> dict:
    """Fetch the request-log row for a response, using its X-Request-ID."""
    entry = client.get(f"/lab/requests/{response.headers['X-Request-ID']}")
    assert entry.status_code == 200, entry.text
    return entry.json()
