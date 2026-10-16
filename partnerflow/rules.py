"""Business rules applied to an application after its JSON is valid.

Three separate things live here. Keeping them apart is the point of the lab:

1. R-101  Intended product limits. Always on. Never bypassed.
2. R-204  A rule added in release B. It is intended for invoice_finance only,
          but release v1.1.0 ships it with a bug that hits every product.
          Patch v1.1.1 fixes the bug and KEEPS the intended rule.
3. toy_decision  A deliberately simple stand-in for a credit decision.
          It is not a credit model and makes no real lending decisions.
"""

from dataclasses import dataclass

from partnerflow import config


@dataclass
class RuleViolation:
    """Why the API refused to accept an application."""

    rule_id: str   # e.g. "R-204"
    code: str      # stable code used in the response and the request log
    message: str   # human-readable explanation


def check_product_limits(product: str, amount: int) -> RuleViolation | None:
    """R-101: the amount must be inside the product's intended limits."""
    limits = config.PRODUCTS[product]
    if limits["min_amount"] <= amount <= limits["max_amount"]:
        return None
    return RuleViolation(
        rule_id="R-101",
        code="rule_R101_product_limits",
        message=(
            f"{product} accepts amounts from {limits['min_amount']:,} to "
            f"{limits['max_amount']:,} GBP. Requested: {amount:,}."
        ),
    )


def check_r204_amount_band(product: str, amount: int, band: list[int],
                           hotfix: bool) -> RuleViolation | None:
    """R-204: only exists in release B (v1.1.0 and v1.1.1).

    Intended behaviour: invoice_finance applications inside the band need
    manual underwriting, so the API does not accept them.
    """
    band_min, band_max = band
    in_band = band_min <= amount <= band_max

    if hotfix:
        # v1.1.1, the targeted fix: the rule checks the product, as intended.
        rule_applies = in_band and product == config.R204_PRODUCT
    else:
        # v1.1.0, THE BUG: the product check is missing, so term_loan and
        # credit_line applications in the band are rejected too.
        rule_applies = in_band

    if not rule_applies:
        return None
    return RuleViolation(
        rule_id="R-204",
        code="rule_R204_amount_band",
        message=(
            f"Applications between {band_min:,} and {band_max:,} GBP need manual "
            f"underwriting and cannot be submitted through the API."
        ),
    )


def check_application(product: str, amount: int, lab: dict) -> RuleViolation | None:
    """Run the rules that are active in the current release, in order."""
    violation = check_product_limits(product, amount)
    if violation:
        return violation
    if lab["release"] == "B":
        return check_r204_amount_band(product, amount, lab["r204_band"], lab["hotfix"])
    return None


def toy_decision(amount: int, annual_turnover: int, trading_months: int) -> tuple[str, str | None]:
    """Return (status, reason). A transparent toy rule, not a credit model."""
    if trading_months < 6:
        return "declined", "trading_history_under_6_months"
    if amount > annual_turnover * 0.5:
        return "declined", "amount_over_half_of_turnover"
    if amount > annual_turnover * 0.25:
        return "referred", "amount_over_quarter_of_turnover"
    return "approved", None
