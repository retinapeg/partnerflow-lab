"""A release comparison needs persisted settings and complete matching evidence."""

import httpx
import pytest

import demo
from partnerflow import analytics, replay, traffic


@pytest.fixture
def baseline(client):
    traffic.generate('before-release', count=12, seed=19, client=client)
    return analytics.request_frame(client.get('/lab/requests', params={'limit': 100}).json())


def test_api_and_sqlite_logs_normalise_identically(baseline):
    local = analytics.load_requests().sort_values('id').reset_index(drop=True)
    remote = baseline.sort_values('id').reset_index(drop=True)
    import pandas as pd
    pd.testing.assert_frame_equal(local, remote)
    assert replay.recorded_settings(remote) == (12, 19)


@pytest.mark.parametrize('corruption', [
    'missing', 'duplicate', 'seed', 'count', 'legacy', 'product', 'amount', 'term_months',
    'partner_id', 'version', 'customer_failure', 'missing_read',
])
def test_invalid_recordings_cannot_be_presented_as_replay(baseline, corruption):
    rows = baseline.copy()
    app = rows.index[rows.request == 'POST /applications'][0]
    if corruption == 'missing':
        rows = rows.drop(index=app)
    elif corruption == 'duplicate':
        import pandas as pd
        rows = pd.concat([rows, rows.loc[[app]]], ignore_index=True)
    elif corruption in ('seed', 'count'):
        rows.loc[app, 'replay_' + corruption] += 1
    elif corruption == 'legacy':
        rows = rows.drop(columns='replay_seed')
    elif corruption in ('product', 'partner_id'):
        rows.loc[app, corruption] = 'different'
    elif corruption in ('amount', 'term_months'):
        rows.loc[app, corruption] += 1
    elif corruption == 'version':
        rows.loc[app, 'release_version'] = 'v1.1.0'
    elif corruption == 'customer_failure':
        rows.loc[rows.request == 'POST /customers', 'status_code'] = 500
    else:
        rows = rows.drop(index=rows.index[rows.request == 'GET /applications/{application_id}'][0])
    with pytest.raises(ValueError, match='reset the lab'):
        replay.recorded_settings(rows)


def test_huge_recorded_count_is_rejected_before_building_journeys(baseline, monkeypatch):
    baseline['replay_count'] = 10**12
    def unexpected(*args):
        pytest.fail('Incomplete run must be rejected before allocating journeys')
    monkeypatch.setattr(replay, 'build_journeys', unexpected)
    with pytest.raises(ValueError):
        replay.recorded_settings(baseline)


@pytest.mark.parametrize('path', ['/lab/reset', '/health', '/lab/release', '/lab/hotfix', '/lab/requests'])
def test_demo_stops_on_failed_control_or_evidence_response(monkeypatch, capsys, path):
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(503 if request.url.path == path else 200,
                              json={'version': 'test', 'faults': {}})
    client = httpx.Client(base_url='http://test', transport=httpx.MockTransport(respond))
    monkeypatch.setattr(demo.httpx, 'Client', lambda **kwargs: client)
    monkeypatch.setattr(demo.traffic, 'generate', lambda *a, **kw: {})
    with pytest.raises(httpx.HTTPStatusError):
        demo.main()
    assert calls[-1] == path
    assert 'POST /applications, identical traffic' not in capsys.readouterr().out
