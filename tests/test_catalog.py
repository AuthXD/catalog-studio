import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from catalog_app import providers
from catalog_app.main import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'demo')
    monkeypatch.setenv('SHOPIFY_WRITES_ENABLED', 'false')
    return create_app(tmp_path / 'catalog.sqlite3')


def register(client, email='owner@example.com'):
    response = client.post('/api/auth/register', json={'email': email, 'name': 'Test Owner', 'password': 'long-test-password'})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        register(client)
        yield client


def analyze(client, kind='description'):
    products = client.get('/api/products').json()
    chosen = next(p for p in products if p['title'] == 'Field tote')
    response = client.post('/api/runs', json={'product_ids': [chosen['id']]})
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed', response.text
    rows = client.get('/api/proposals').json()
    return chosen, next(p for p in rows if p['kind'] == kind)


def execute(client, proposal, key='operation-key-1', dry=False):
    return client.post(f"/api/proposals/{proposal['id']}/execute", json={'dry_run': dry, 'idempotency_key': key})


def approve(client, proposal):
    return client.post(f"/api/proposals/{proposal['id']}/decision", json={'decision': 'approved'})


def test_auth_cookie_logout_and_password_storage(app, client):
    assert client.get('/api/me').status_code == 200
    assert 'HttpOnly' in client.post('/api/auth/login', json={'email': 'owner@example.com', 'password': 'long-test-password'}).headers['set-cookie']
    with app.state.catalog.db() as db:
        password = db.execute('SELECT password FROM users').fetchone()[0]
        assert password != 'long-test-password' and len(password.split(':')[1]) == 64
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/products').status_code == 401


def test_complete_graph_analysis_and_dry_run(client):
    original, proposal = analyze(client)
    assert proposal['status'] == 'proposed'
    assert len(client.get('/api/proposals').json()) == 3
    assert execute(client, proposal).status_code == 409
    assert approve(client, proposal).status_code == 200
    before = client.get('/api/products').json()
    assert execute(client, proposal, dry=True).json()['writes'] == 0
    assert client.get('/api/products').json() == before
    assert client.get('/api/history').json()['operations'] == []
    result = execute(client, proposal)
    assert result.status_code == 200 and result.json()['status'] == 'applied'
    after = next(p for p in client.get('/api/products').json() if p['id'] == original['id'])
    assert after['description'] == proposal['after_text']
    assert after['version'] == original['version'] + 1


def test_idempotent_execution(client):
    _, p = analyze(client)
    approve(client, p)
    first = execute(client, p).json()
    second = execute(client, p).json()
    assert second['id'] == first['id'] and second['replayed']
    assert len(client.get('/api/history').json()['operations']) == 1
    assert execute(client, p, key='another-key').status_code == 409


def test_immutable_rejection_and_decision(client):
    _, p = analyze(client)
    assert client.post(f"/api/proposals/{p['id']}/decision", json={'decision': 'rejected'}).status_code == 200
    assert approve(client, p).status_code == 409
    assert execute(client, p).status_code == 409


def test_stale_product_refuses_execution(client):
    original, p = analyze(client)
    approve(client, p)
    edited = {**original, 'title': 'Changed title', 'expected_version': original['version']}
    assert client.put('/api/products/' + original['id'], json=edited).status_code == 200
    assert execute(client, p).status_code == 409


def test_new_evidence_invalidates_old_proposal(client):
    product, p = analyze(client)
    approve(client, p)
    response = client.post(f"/api/products/{product['id']}/evidence", json={
        'source': 'https://example.com/exact-product', 'price': 35, 'currency': 'USD',
        'observed_at': datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 200
    assert execute(client, p).status_code == 409


def test_owner_isolation(app, client):
    product, proposal = analyze(client)
    with TestClient(app) as other:
        register(other, 'other@example.com')
        assert product['id'] not in [p['id'] for p in other.get('/api/products').json()]
        assert other.get('/api/proposals').json() == []
        assert approve(other, proposal).status_code == 404
        assert execute(other, proposal, dry=True).status_code == 404
        assert other.post('/api/runs', json={'product_ids': [product['id']]}).status_code == 404


def test_restock_does_not_change_physical_inventory(client):
    product, p = analyze(client, 'restock')
    approve(client, p)
    result = execute(client, p).json()
    assert result['status'] == 'applied' and result['writes'] == 0
    after = next(x for x in client.get('/api/products').json() if x['id'] == product['id'])
    assert after['inventory'] == product['inventory']


def test_promotion_observations_explicitly_sample(client):
    product, p = analyze(client, 'promotion')
    assert p['discount_pct'] == 10
    assert p['evidence'][0]['is_sample'] == 1
    assert 'sample' in p['evidence'][0]['source']
    approve(client, p)
    assert execute(client, p).json()['code'].startswith('CATALOG-')


def test_boundary_validation_and_csrf(client):
    assert client.post('/api/runs', json={'product_ids': []}).status_code == 422
    product = {'title': 'Thing', 'description': 'Facts', 'price': -1, 'currency': 'USD', 'inventory': 0}
    assert client.post('/api/products', json=product).status_code == 422
    assert client.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/auth/logout', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_provider_failure_persisted(client, monkeypatch):
    def broken(_):
        raise providers.ProviderError('Provider unavailable')
    monkeypatch.setattr(providers, 'description', broken)
    product = client.get('/api/products').json()[0]
    response = client.post('/api/runs', json={'product_ids': [product['id']]}).json()
    assert response['status'] == 'failed' and response['error'] == 'Provider unavailable'
    assert client.get('/api/proposals').json() == []
    assert client.get('/api/history').json()['runs'][0]['status'] == 'failed'


def test_live_write_disabled_fails_closed(client, app):
    product, p = analyze(client)
    approve(client, p)
    with app.state.catalog.db() as db:
        db.execute("UPDATE products SET origin='shopify',remote_id='gid://shopify/Product/123' WHERE id=?", (product['id'],))
    result = execute(client, p).json()
    assert result['status'] == 'failed'
    assert 'disabled' in result['message']


def test_uncertain_remote_write_and_manual_reconciliation(client, app, monkeypatch):
    product, p = analyze(client)
    approve(client, p)
    with app.state.catalog.db() as db:
        db.execute("UPDATE products SET origin='shopify' WHERE id=?", (product['id'],))
    def uncertain(*_):
        raise providers.UnknownOutcomeError('Timed out after write')
    monkeypatch.setattr(providers, 'write_shopify', uncertain)
    result = execute(client, p).json()
    assert result['status'] == 'unknown'
    assert execute(client, p).json()['replayed']
    reconcile = client.post('/api/operations/' + result['id'] + '/reconcile', json={
        'confirmed_applied': True, 'note': 'Verified exact description in store admin.'})
    assert reconcile.status_code == 200 and reconcile.json()['status'] == 'applied'


def test_invalid_model_response_fails_analysis(client, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    import httpx
    monkeypatch.setattr(providers.httpx, 'post', lambda *a, **k: httpx.Response(200, json={'output': []}, request=httpx.Request('POST', 'https://offline.invalid')))
    product = client.get('/api/products').json()[0]
    response = client.post('/api/runs', json={'product_ids': [product['id']]}).json()
    assert response['status'] == 'failed'
    assert client.get('/api/proposals').json() == []


def test_shopify_discount_is_product_scoped_and_true_code(monkeypatch):
    monkeypatch.setenv('SHOPIFY_WRITES_ENABLED', 'true')
    calls=[]
    def fake(query, variables=None, mutating=False):
        calls.append((query, variables, mutating))
        return {'discountCodeBasicCreate': {'codeDiscountNode': {'id': 'gid://shopify/DiscountCodeNode/1'}, 'userErrors': []}} if mutating else {'product': {'updatedAt': 'v1'}}
    monkeypatch.setattr(providers, 'shopify', fake)
    result = providers.write_shopify({'remote_id': 'gid://shopify/Product/123', 'remote_version': 'v1'},
        {'kind': 'promotion', 'discount_pct': 10}, 'abc123')
    assert result['codeDiscountNode']['id']
    payload = calls[-1][1]['input']
    assert payload['customerGets']['items']['products']['productsToAdd'] == ['gid://shopify/Product/123']
    assert payload['code'] == 'CATALOG-ABC123'


def test_malformed_shopify_mutation_is_uncertain(monkeypatch):
    monkeypatch.setenv('SHOPIFY_WRITES_ENABLED', 'true')
    def fake(query, variables=None, mutating=False):
        return {} if mutating else {'product': {'updatedAt': 'v1'}}
    monkeypatch.setattr(providers, 'shopify', fake)
    with pytest.raises(providers.UnknownOutcomeError):
        providers.write_shopify({'remote_id': 'gid://shopify/Product/123', 'remote_version': 'v1'},
            {'kind': 'promotion', 'discount_pct': 10}, 'abc123')


def test_export_is_owned_json(client):
    response = client.get('/api/export')
    assert response.status_code == 200
    assert 'attachment' in response.headers['content-disposition']
    assert len(response.json()['products']) == 4
