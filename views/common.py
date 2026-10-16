"""Helpers shared by the dashboard pages.

The dashboard talks to the lab in two ways:

1. Over HTTP, exactly like a partner would (call_api). Used for every action:
   sending requests, switching releases, toggling faults, reading /metrics.
2. Reading the SQLite file directly, read-only, with pandas (load_log). Used
   for analysis, where a whole table is needed at once.
"""

import shlex

import httpx
import pandas as pd
import streamlit as st

from partnerflow import analytics, config, traffic

RUN_ORDER = ["before-release", "after-release", "after-fix"]


def call_api(method: str, path: str, **kwargs) -> httpx.Response:
    """Send one HTTP request to the lab API."""
    return httpx.request(method, config.api_url() + path, timeout=30, **kwargs)


def get_json(path: str, **params):
    response = call_api("GET", path, params=params or None)
    response.raise_for_status()
    return response.json()


def api_is_up() -> bool:
    try:
        return call_api("GET", "/health").status_code == 200
    except httpx.HTTPError:
        return False


def load_log() -> pd.DataFrame:
    """The whole request log as a DataFrame (read-only SQLite query)."""
    return analytics.load_requests()


def generate_traffic(run_label: str, count: int, seed: int) -> dict:
    with st.spinner(f"Sending {count} customer journeys to the API as '{run_label}'..."):
        return traffic.generate(run_label, count, seed)


def pct(value, digits: int = 1) -> str:
    """0.1234 -> '12.3%'. Shows a dash when there is no data."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:.{digits}f}%"


def points(value, digits: int = 1) -> str:
    """0.05 -> '+5.0 pts'. For differences between two percentages."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value * 100:+.{digits}f} pts"


def p_value_text(p_value: float) -> str:
    return "p < 0.001" if p_value < 0.001 else f"p = {p_value:.3f}"


def tile(column, label: str, value, note: str | None = None, help: str | None = None) -> None:
    """A bordered number tile. `note` is plain small print, not an up/down change."""
    column.metric(label, value, note, delta_color="off", delta_arrow="off", border=True, help=help)


def with_p_text(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy of a table with its p_value column written as readable text."""
    frame = frame.copy()
    frame["p_value"] = ["< 0.001" if p < 0.001 else f"{p:.3f}" for p in frame["p_value"]]
    return frame


def curl_command(method: str, path: str, headers: dict | None = None, body: str | None = None) -> str:
    """The equivalent curl command for a request, safe to paste into a terminal."""
    parts = ["curl", "-i"]
    if method != "GET":
        parts += ["-X", method]
    parts.append(shlex.quote(config.api_url() + path))
    for name, value in (headers or {}).items():
        parts += ["-H", shlex.quote(f"{name}: {value}")]
    if body is not None:
        parts += ["-H", shlex.quote("Content-Type: application/json"), "-d", shlex.quote(body)]
    return " ".join(parts)


def go_to(page: str) -> None:
    """Button callback: switch the sidebar to another page."""
    st.session_state["page"] = page


def show_table(frame: pd.DataFrame, percent_columns: tuple[str, ...] = (),
               column_config: dict | None = None, **kwargs):
    """st.dataframe with percentage columns formatted, and no index column.

    `column_config` adds or overrides formatting for other columns.
    """
    config = {
        name: st.column_config.NumberColumn(format="percent") for name in percent_columns
        if name in frame.columns
    }
    config.update(column_config or {})
    return st.dataframe(frame, hide_index=True, column_config=config, **kwargs)
