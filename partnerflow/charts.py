"""Small chart helpers for the dashboard, built on Altair.

Three chart types cover everything the dashboard shows:

* bars      - compare a number across a few named groups
* intervals - an estimate with its confidence interval
* heatmap   - a rate across two dimensions at once

Colour is used sparingly: one blue for data, grey for context. A value label
sits next to each mark so nothing depends on colour or on hovering.

Streamlit fits the whole chart, axis included, into the given height. So each
height below is "room per row" plus a fixed allowance for the axis.
"""

import altair as alt
import pandas as pd

BLUE = "#2a78d6"
GREY = "#c4c3bd"
INK = "#0b0b0b"
BLUE_RAMP = ["#cde2fb", "#0d366b"]   # light to dark, for "more is darker"
BAND = "#ecebe7"                     # the "normal range" band on control charts
CRITICAL = "#d03b3b"                 # status colour: reserved for flagged points
AXIS_ROOM = 64                       # pixels kept for the value axis and its title
LABEL_WIDTH = 340                    # widest row label before it is cut short


def bars(data: pd.DataFrame, category: str, value: str, value_format: str = ".1%",
         value_title: str | None = None, highlight: list | None = None,
         domain: list | None = None) -> alt.Chart:
    """Horizontal bars with the value written at the end of each bar.

    `highlight` paints the named categories blue and the rest grey, to draw
    the eye to the one group that matters.
    """
    scale = alt.Scale(domain=domain) if domain else alt.Undefined
    base = alt.Chart(data).encode(
        y=alt.Y(f"{category}:N", sort=None, title=None, axis=alt.Axis(labelLimit=LABEL_WIDTH)),
        x=alt.X(f"{value}:Q", title=value_title, scale=scale,
                axis=alt.Axis(format=value_format, tickCount=5)),
        tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip(f"{value}:Q", format=value_format)],
    )
    if highlight is None:
        colour = alt.value(BLUE)
    else:
        colour = alt.condition(alt.FieldOneOfPredicate(field=category, oneOf=highlight),
                               alt.value(BLUE), alt.value(GREY))
    marks = base.mark_bar(size=18, cornerRadiusEnd=4).encode(color=colour)
    labels = base.mark_text(align="left", dx=6, color=INK).encode(
        text=alt.Text(f"{value}:Q", format=value_format))
    return (marks + labels).properties(height=34 * len(data) + AXIS_ROOM)


def intervals(data: pd.DataFrame, category: str, value: str, low: str, high: str,
              value_format: str = ".1%", value_title: str | None = None,
              reference: float | None = None) -> alt.Chart:
    """A dot for each estimate and a line for its confidence interval.

    `reference` draws a grey vertical line, e.g. at zero for "no effect".
    """
    base = alt.Chart(data).encode(
        y=alt.Y(f"{category}:N", sort=None, title=None, axis=alt.Axis(labelLimit=LABEL_WIDTH)))
    tooltip = [alt.Tooltip(f"{category}:N"),
               alt.Tooltip(f"{value}:Q", format=value_format, title="estimate"),
               alt.Tooltip(f"{low}:Q", format=value_format, title="interval low"),
               alt.Tooltip(f"{high}:Q", format=value_format, title="interval high")]
    line = base.mark_rule(strokeWidth=2, color=BLUE).encode(
        x=alt.X(f"{low}:Q", title=value_title, scale=alt.Scale(zero=False, padding=30),
                axis=alt.Axis(format=value_format, tickCount=6)),
        x2=f"{high}:Q", tooltip=tooltip)
    dot = base.mark_point(filled=True, size=110, color=BLUE, opacity=1).encode(
        x=f"{value}:Q", tooltip=tooltip)
    label = base.mark_text(dy=-14, color=INK).encode(
        x=f"{value}:Q", text=alt.Text(f"{value}:Q", format=value_format))
    layers = [line, dot, label]
    if reference is not None:
        marker = alt.Chart(pd.DataFrame({"reference": [reference]})).mark_rule(
            color=GREY, strokeWidth=1.5).encode(x="reference:Q")
        layers.insert(0, marker)
    return alt.layer(*layers).properties(height=56 * len(data) + AXIS_ROOM)


def heatmap(data: pd.DataFrame, x: str, y: str, value: str, value_format: str = ".0%",
            x_order: list | None = None, y_order: list | None = None) -> alt.Chart:
    """A grid of cells, darker for higher values, with the value written in each.

    There is no colour legend: every cell already shows its number.
    """
    base = alt.Chart(data).encode(
        x=alt.X(f"{x}:N", sort=x_order, title=None,
                axis=alt.Axis(labelAngle=0, orient="top"), scale=alt.Scale(paddingInner=0.06)),
        y=alt.Y(f"{y}:N", sort=y_order, title=None, scale=alt.Scale(paddingInner=0.1)),
        tooltip=[alt.Tooltip(f"{y}:N"), alt.Tooltip(f"{x}:N"),
                 alt.Tooltip(f"{value}:Q", format=value_format)],
    )
    cells = base.mark_rect(cornerRadius=3).encode(
        color=alt.Color(f"{value}:Q", legend=None,
                        scale=alt.Scale(domain=[0, 1], range=BLUE_RAMP)))
    text = base.mark_text().encode(
        text=alt.Text(f"{value}:Q", format=value_format),
        color=alt.condition(alt.datum[value] > 0.45, alt.value("white"), alt.value(INK)))
    return (cells + text).properties(height=52 * data[y].nunique() + 40)


def control_chart(data: pd.DataFrame, x: str = "timestamp", observed: str = "observed_rate",
                  expected: str = "expected_rate", low: str = "lcl", high: str = "ucl",
                  flagged: str = "flagged", value_format: str = ".1%",
                  height: int = 180) -> alt.Chart:
    """A control chart: observed rate over time, expected rate, and the 3-sigma band.

    Flagged points are drawn in the status colour for 'critical'. Red is only
    ever used for that, so a red dot always means "this hour was unusual".
    """
    base = alt.Chart(data).encode(
        x=alt.X(f"{x}:T", title=None, axis=alt.Axis(format="%a %d", tickCount="day", grid=False)))
    tooltip = [alt.Tooltip(f"{x}:T", format="%a %d %b %H:%M", title="hour"),
               alt.Tooltip("requests:Q", format=",d"), alt.Tooltip("failures:Q", format=",d"),
               alt.Tooltip(f"{observed}:Q", format=value_format, title="observed"),
               alt.Tooltip(f"{expected}:Q", format=value_format, title="expected"),
               alt.Tooltip("z:Q", format=".1f"), alt.Tooltip("p_value:Q", format=".2g")]
    band = base.mark_area(color=BAND, opacity=1).encode(
        y=alt.Y(f"{low}:Q", title=None, axis=alt.Axis(format=value_format, tickCount=4)),
        y2=f"{high}:Q")
    expected_line = base.mark_line(color=GREY, strokeWidth=1.5).encode(y=f"{expected}:Q")
    observed_line = base.mark_line(color=BLUE, strokeWidth=1.5).encode(y=f"{observed}:Q", tooltip=tooltip)
    flags = base.transform_filter(alt.FieldEqualPredicate(field=flagged, equal=True)).mark_point(
        filled=True, size=70, color=CRITICAL, opacity=1).encode(y=f"{observed}:Q", tooltip=tooltip)
    return alt.layer(band, expected_line, observed_line, flags).properties(height=height)
