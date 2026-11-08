"""Exercise SDK discovery and calls, including actual MCP dispatch, without external APIs."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import httpx
import pytest

from catalog_app.mcp_server import CatalogAPI, make_server


def test_mcp_protocol_discovery_and_owner_scoped_reads():
    calls = []

    class API:
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return [{'id': 'owned-product'}]

    async def run():
        server = make_server(API())
        tools = await server.list_tools()
        assert {t.name for t in tools} == {
            'list_catalog', 'analyze_products', 'list_proposals', 'validate_approved_proposal'}
        result = await server.call_tool('list_catalog', {})
        assert not result.is_error
        assert calls == [('GET', '/api/products', None)]
    asyncio.run(run())


def test_mcp_validation_cannot_disable_dry_run():
    calls = []

    class API:
        def request(self, method, path, body=None):
            calls.append(body)
            return {'status': 'validated'}

    async def run():
        server = make_server(API())
        tools = await server.list_tools()
        validate_tool = next(t for t in tools if t.name == 'validate_approved_proposal')
        assert 'dry_run' not in validate_tool.input_schema.get('properties', {})
        result = await server.call_tool('validate_approved_proposal', {'proposal_id': 'abc-123'})
        assert not result.is_error
    asyncio.run(run())
    assert calls[0]['dry_run'] is True


def test_mcp_rejects_remote_credentials_destination(monkeypatch):
    monkeypatch.setenv('CATALOG_API_URL', 'https://example.com')
    with pytest.raises(ValueError, match='localhost'):
        CatalogAPI()


def test_bridge_authenticates_and_does_not_follow_redirects(monkeypatch):
    monkeypatch.setenv('CATALOG_MCP_EMAIL', 'test@example.test')
    monkeypatch.setenv('CATALOG_MCP_PASSWORD', 'test-password')
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith('/login'):
            return httpx.Response(200, json={'id': 'owner'}, headers={'Set-Cookie': 'catalog_session=local-token; Path=/'})
        assert 'catalog_session=local-token' in request.headers['cookie']
        return httpx.Response(200, json=[{'id': 'owned'}])

    client = httpx.Client(base_url='http://127.0.0.1:8023', transport=httpx.MockTransport(respond))
    api = CatalogAPI(client)
    assert api.request('GET', '/api/products') == [{'id': 'owned'}]
    api.request('GET', '/api/products')
    assert requests == ['/api/auth/login', '/api/products', '/api/products']


def test_real_stdio_handshake_and_discovery():
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'local-verification', 'version': '1.0'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
    ]
    result = subprocess.run([sys.executable, '-m', 'catalog_app.mcp_server'],
        cwd=Path(__file__).resolve().parents[1],
        input=''.join(json.dumps(m) + '\n' for m in messages),
        text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    replies = {item['id']: item for line in result.stdout.splitlines()
               if (item := json.loads(line)).get('id') is not None}
    assert 'result' in replies[1]
    assert {t['name'] for t in replies[2]['result']['tools']} == {
        'list_catalog', 'analyze_products', 'list_proposals', 'validate_approved_proposal'}
