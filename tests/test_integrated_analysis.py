"""Offline integration: validated stream, staff-only wording and degraded coverage."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from analysis_scaffold.analyzer import Flag
from analysis_scaffold.deterministic_analyzer import MESSAGES
from analysis_scaffold.llm_analyzer import FakeLLMClient, LLMAnalyzer
from body_heat_demo import server, views
from body_heat_demo.engine import build_demo
from f1_sideline_demo.hub import Rejected
from test_demo_prototype import packet


class Spy:
    def __init__(self):
        self.calls = []

    def ingest(self, pkt, t):
        self.calls.append((pkt, t))
        return [Flag(t, pkt['device_id'], 'trend', MESSAGES['trend'])]


def test_only_accepted_packets_reach_analyzer():
    _, hub, _, roster, _ = build_demo(seed_history=False)
    spy = hub.analyzer = Spy()
    for bad in [{}, packet(dev='pod-99'), packet(tag='bad'), packet(dev='pod-33')]:
        with pytest.raises(Rejected):
            hub.accept(bad, 7)
    hub.accept(packet(), 8)
    with pytest.raises(Rejected):
        hub.accept(packet(), 9)
    assert spy.calls == [(packet(), 8)]
    tile = views.build_tile(hub, roster[0], 8)
    assert tile['hub_analysis']['flag'] == {'t': 8, 'kind': 'trend', 'message': MESSAGES['trend']}
    assert 'hub_analysis' not in views.build_tile(hub, roster[0], 8, staff=False)


@pytest.mark.parametrize('name', ['deterministic', 'llm-explain', 'llm-rolling'])
def test_script_flags_exact_wording_and_fake_without_key(name, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    _, hub, _, roster, engine = build_demo(seed_history=False, analyzer=name)
    if name.startswith('llm'):
        assert isinstance(hub.analyzer.client, FakeLLMClient)
    member = next(m for m in roster if m['device_id'] == 'pod-11')
    seen = {}
    for _ in range(200):
        engine.step()
        flag = views.build_tile(hub, member, engine.t)['hub_analysis']['flag']
        if flag:
            seen[flag['kind']] = flag['message']
    assert seen == MESSAGES


@pytest.mark.parametrize('scope', ['wearer', 'session'])
def test_budget_visibly_surfaces(scope):
    _, hub, _, roster, engine = build_demo(seed_history=False)
    hub.analyzer = LLMAnalyzer(variant='rolling', **{f'max_calls_{"per_wearer" if scope == "wearer" else "session"}': 0})
    engine.step()
    for m in roster[:-1]:
        analysis = views.build_tile(hub, m, engine.t)['hub_analysis']
        assert analysis['flag'] is None
        assert analysis['status'] == 'Analysis unavailable'
        notice = analysis['notice']
        assert notice['kind'] == 'budget_exhausted'
        assert notice['message'] == 'Model analysis unavailable: call budget exhausted.'


def test_unavailable_never_looks_like_ok():
    class Failing(FakeLLMClient):
        def complete(self, window):
            raise RuntimeError('private provider detail')
    _, hub, _, roster, engine = build_demo(seed_history=False)
    hub.analyzer = LLMAnalyzer(Failing(), variant='rolling')
    engine.step()
    a = views.build_tile(hub, roster[0], engine.t)['hub_analysis']
    assert a['notice'] == {'t': 0, 'kind': 'analysis unavailable', 'message': 'analysis unavailable'}
    assert a['flag'] is None
    assert a['status'] == 'Analysis unavailable'


def test_relay_prior_payload():
    _, hub, _, roster, engine = build_demo(seed_history=False, analyzer='relay')
    engine.step()
    before = views.build_roster(hub, roster, engine.t)
    hub.analyzer = None
    assert before == views.build_roster(hub, roster, engine.t)
    assert all('hub_analysis' not in tile for tile in before)


def test_athlete_json_and_sse_have_no_analysis():
    store, hub, accounts, roster, engine = build_demo(seed_history=False)
    engine.step()
    server.STATE.update(store=store, hub=hub, accounts=accounts, roster=roster, engine=engine)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    token = accounts.sign_in('athlete11', 'demo')
    base = f'http://127.0.0.1:{srv.server_port}'
    try:
        for path in ['/api/wearer/pod-11', '/api/events']:
            req = urllib.request.Request(base+path, headers={'Cookie': f'bh_session={token}'})
            with urllib.request.urlopen(req, timeout=3) as response:
                raw = response.readline() if path.endswith('events') else response.read()
                assert b'hub_analysis' not in raw
                assert b'analysis unavailable' not in raw
        req = urllib.request.Request(base+'/api/roster', headers={'Cookie': f'bh_session={token}'})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_analysis_status_wording_and_flag_precedence():
    _, hub, _, roster, _ = build_demo(seed_history=False)
    member = roster[0]
    dev = member['device_id']
    assert views.build_tile(hub, member, 0)['hub_analysis']['status'] == 'Awaiting analysis output'
    hub.analysis_notices[dev] = {'t': 0, 'kind': 'analysis unavailable', 'message': 'analysis unavailable'}
    hub.analysis_flags[dev] = {'t': 0, 'kind': 'trend', 'message': MESSAGES['trend']}
    assert views.build_tile(hub, member, 0)['hub_analysis']['status'] == 'Latest analysis flag'


def test_pod_guidance_exact_wording_served(monkeypatch):
    store, hub, accounts, roster, engine = build_demo(seed_history=False)
    monkeypatch.setattr(server, 'STATE', dict(store=store, hub=hub, accounts=accounts, roster=roster, engine=engine))
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{srv.server_port}/api/strings', timeout=3) as response:
            strings = json.load(response)['strings']
        for guidance in [
            'Readings are above the team-set limit; staff review requested.',
            'Readings show a trend above the team-set limit; staff review requested.',
            'Your readings are above the team-set limit. Your staff can see this too.',
            'Your readings are rising faster than the team-set limit. Your staff can see this too.',
        ]:
            assert strings.count(guidance) == 1
    finally:
        srv.shutdown()
        srv.server_close()


# -- fixes from the Nov 2026 review ------------------------------------------------
@pytest.fixture
def live(monkeypatch):
    store, hub, accounts, roster, engine = build_demo(autoplay=True, seed_history=False)
    monkeypatch.setattr(server, 'STATE', dict(store=store, hub=hub, accounts=accounts, roster=roster, engine=engine))
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port, hub, accounts, engine
    finally:
        srv.shutdown()
        srv.server_close()


def _raw_get(port, path):
    """Send the path byte for byte (urllib would normalise '..')."""
    import socket
    with socket.create_connection(('127.0.0.1', port), timeout=3) as s:
        s.sendall(f'GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n'.encode())
        data = b''
        while chunk := s.recv(65536):
            data += chunk
    return int(data.split(b' ', 2)[1])


def test_static_files_cannot_escape_the_static_folder(live):
    port = live[0]
    assert _raw_get(port, '/static/app.js') == 200
    for path in ['/static/../server.py', '/static/../../README.md', '/static/..%2fserver.py',
                 '/static//etc/hosts', '/static/']:
        assert _raw_get(port, path) == 404, path


def _post(port, path, body, token):
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(body).encode(),
                                 headers={'Cookie': f'bh_session={token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code


def test_unknown_wearer_requests_change_nothing_and_the_clock_keeps_running(live):
    port, hub, accounts, engine = live
    token = accounts.sign_in('k.smith', 'demo')
    for path, body in [('/api/consent', {'device_id': 'pod-99', 'consented': True}),
                       ('/api/ack', {'device_id': 'pod-99'}),
                       ('/api/alerts_only', {'device_id': 'pod-99', 'on': True})]:
        assert _post(port, path, body, token) == 404
    assert _post(port, '/api/ack', ['not', 'an', 'object'], token) == 400
    assert 'pod-99' not in hub._enrolled and 'pod-99' not in hub.alerts_only
    engine.t = 154                      # scripted unknown-pod packet
    engine.step()
    assert hub.rejections['unknown'] == 1


def test_session_settings_come_from_the_running_code(live):
    port, _, accounts, _ = live
    from body_heat_demo import engine as eng_mod, hub as hub_mod
    token = accounts.sign_in('k.smith', 'demo')
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/session', headers={'Cookie': f'bh_session={token}'})
    with urllib.request.urlopen(req, timeout=3) as response:
        settings = json.load(response)['settings']
    assert settings == {'trend_limit_c_per_5min': eng_mod.TREND_LIMIT_C_PER_5MIN,
                        'trend_window_s': eng_mod.TREND_WINDOW_S,
                        'absolute_limit_c': eng_mod.ABSOLUTE_LIMIT_C,
                        'recovery_period_s': hub_mod.RECOVERY_PERIOD_S,
                        'low_battery_pct': hub_mod.LOW_BATTERY_PCT,
                        'no_signal_after_s': views.NO_SIGNAL_AFTER_S}


def test_live_llm_budgets_are_sized_for_the_session_not_the_replay():
    from analysis_scaffold.llm_analyzer import call_budgets
    _, hub, _, roster, engine = build_demo(seed_history=False, analyzer='llm-rolling', session_minutes=10)
    assert (hub.analyzer.max_calls_per_wearer, hub.analyzer.max_calls_session) == call_budgets(600, len(roster))
    for _ in range(600):                # 10 minutes: the 200 s replay budget ran out at t=241
        engine.step()
    assert not [n for n in hub.analysis_notices.values() if n['kind'] == 'budget_exhausted']


def test_live_grid_hub_line_and_no_flags_yet():
    _, hub, _, roster, engine = build_demo(seed_history=False)
    by_dev = {m['device_id']: m for m in roster}
    a = views.build_tile(hub, by_dev['pod-02'], 0)['hub_analysis']
    assert (a['status'], a['compact']) == ('Awaiting analysis output', 'Hub: waiting for readings')
    engine.step()
    a = views.build_tile(hub, by_dev['pod-02'], engine.t)['hub_analysis']
    assert (a['status'], a['compact']) == ('No flags yet', 'Hub: no flags yet')
    for _ in range(95):
        engine.step()
    a = views.build_tile(hub, by_dev['pod-11'], engine.t)['hub_analysis']
    assert a['compact'] == 'Hub flag: above limit · 1:30'
    assert a['flag']['message'] == MESSAGES['above_limit']      # full wording kept for the detail view
    assert 'hub_analysis' not in views.build_tile(hub, by_dev['pod-33'], engine.t)   # no consent, no analysis
