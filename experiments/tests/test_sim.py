import hashlib

import numpy as np
import pandas as pd

from sim import generate
from sim.generate import write_data


def test_reproducible_files(tmp_path):
    write_data(0, tmp_path)
    first = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    write_data(0, tmp_path)
    assert first == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    assert not generate(1)[0].equals(pd.read_parquet(tmp_path / "events.parquet"))


def test_contract_and_funnel(simulation):
    events, truth = simulation
    assert list(events.columns) == [
        "customer_id", "partner_id", "partner_size", "arrival_day", "week",
        "requested_amount", "trading_age_months", "sector", "prequal_shown",
        "topup_notified", "stage_data_complete", "routed_auto", "stage_offer",
        "stage_accepted", "stage_drawdown", "repeat_90d",
    ]
    assert events.customer_id.is_unique
    assert set(events.partner_id) == set(range(40))
    assert events.arrival_day.between(0, 139).all()
    assert (events.week == events.arrival_day // 7).all()
    assert not events.isna().any().any()
    stages = ["stage_data_complete", "stage_offer", "stage_accepted", "stage_drawdown", "repeat_90d"]
    for previous, current in zip(stages, stages[1:]):
        assert (~events[current] | events[previous]).all()
    starts = events.partner_id.map({int(k): v for k, v in truth["prequal_start_day"].items()})
    assert (events.prequal_shown == (events.arrival_day >= starts)).all()
    assert (~events.topup_notified | (events.stage_drawdown & (events.arrival_day >= 70))).all()
    eligible = events.query("stage_drawdown and arrival_day >= 70")
    assert abs(eligible.topup_notified.mean() - 0.5) < 0.02
    sizes = events.groupby("partner_id").size()
    start_values = np.array([truth["prequal_start_day"][str(i)] for i in sizes.index])
    assert np.corrcoef(sizes, start_values)[0, 1] < -0.8
