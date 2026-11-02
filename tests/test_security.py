import os
import sys
import types
from unittest.mock import patch

os.environ['TRAVEL_STATELESS'] = '1'
os.environ['SESSION_SECRET'] = 'test-only-session-key-for-security-checks'

from fastapi.testclient import TestClient
import app


def client():
    app._attempts.clear()
    c = TestClient(app.app)
    c.get('/')
    return c


def test_security_headers_and_private_files():
    c = client()
    r = c.get('/')
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers['content-security-policy']
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert r.headers['cache-control'] == 'no-store'
    cookie_response = TestClient(app.app).get('/')
    assert 'httponly' in cookie_response.headers['set-cookie'].lower()
    for path in ['/.env', '/.git/config', '/backend.py', '/requirements.txt', '/docs', '/openapi.json', '/static/../.env']:
        assert c.get(path).status_code == 404, path


def test_rejects_cross_origin_and_foreign_thread():
    c = client()
    assert c.post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert c.post('/api/travel', json={'message': 'Japan', 'thread_id': 'someone-elses-id'}, headers={'Origin': 'http://testserver'}).status_code == 403
    assert TestClient(app.app).post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'http://testserver'}).status_code == 403


def test_limits_input_and_omits_validation_values():
    c = client()
    for payload in [{'message': ''}, {'message': 'x' * 2001}, {'message': 'Japan', 'unexpected': 'secret-value'}]:
        r = c.post('/api/travel', json=payload, headers={'Origin': 'http://testserver'})
        assert r.status_code == 422
        assert 'secret-value' not in r.text
    assert c.post('/api/travel', content=b'x' * 8193).status_code == 413


def test_provider_errors_and_secrets_are_not_returned():
    c = client()
    fake = types.ModuleType('backend')
    fake.run_travel_agent = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('https://provider?api_key=secret-value'))
    with patch.dict(sys.modules, {'backend': fake}):
        r = c.post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'http://testserver'})
    assert r.status_code == 503
    assert 'secret-value' not in r.text
    fake.run_travel_agent = lambda **kwargs: {'answer': 'test-provider-secret', 'thread_id': kwargs['thread_id']}
    with patch.dict(sys.modules, {'backend': fake}), patch.dict(os.environ, {'TEST_API_KEY': 'test-provider-secret'}):
        r = c.post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'http://testserver'})
    assert r.status_code == 200
    assert r.json()['answer'] == '[redacted]'


def test_rate_limit_prevents_provider_calls():
    c = client()
    fake = types.ModuleType('backend')
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return {'answer': 'Travel plan', 'thread_id': kwargs['thread_id']}
    fake.run_travel_agent = run
    with patch.dict(sys.modules, {'backend': fake}):
        for _ in range(5):
            assert c.post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'http://testserver'}).status_code == 200
        assert c.post('/api/travel', json={'message': 'Japan'}, headers={'Origin': 'http://testserver'}).status_code == 429
    assert len(calls) == 5
