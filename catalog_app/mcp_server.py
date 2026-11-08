"""Authenticated localhost MCP tools. Approval and real execution stay in the UI."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from mcp.server import MCPServer


class CatalogAPI:
    def __init__(self, client: httpx.Client | None = None):
        address = os.getenv('CATALOG_API_URL', 'http://127.0.0.1:8023')
        parsed = urlsplit(address)
        if (parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
                or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise ValueError('CATALOG_API_URL must be a localhost HTTP address without credentials or a path.')
        self.client = client or httpx.Client(base_url=address, timeout=90, follow_redirects=False)
        self.authenticated = False

    def request(self, method: str, path: str, body: dict | None = None):
        if not self.authenticated:
            email = os.getenv('CATALOG_MCP_EMAIL', '')
            password = os.getenv('CATALOG_MCP_PASSWORD', '')
            if not email or not password:
                raise ValueError('Set CATALOG_MCP_EMAIL and CATALOG_MCP_PASSWORD for an existing Catalog Studio account.')
            login = self.client.post('/api/auth/login', json={'email': email, 'password': password})
            if login.status_code != 200:
                raise ValueError('Catalog Studio sign-in failed. Check the local account credentials.')
            self.authenticated = True
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            if response.status_code == 401:
                self.authenticated = False
            raise ValueError(f'Catalog Studio rejected the request with HTTP {response.status_code}.')
        return response.json()


def make_server(api: CatalogAPI | None = None) -> MCPServer:
    server = MCPServer('Catalog Studio', instructions=(
        'Read a signed-in user catalog, create proposals, and validate approved changes. '
        'Tools cannot approve, reject, or apply changes. The user makes those decisions in Catalog Studio.'
    ))
    bridge = api

    def request(method, path, body=None):
        nonlocal bridge
        if bridge is None:
            bridge = CatalogAPI()
        return bridge.request(method, path, body)

    @server.tool()
    def list_catalog() -> list[dict]:
        """Read only the authenticated account's catalog and its observed price evidence."""
        return request('GET', '/api/products')

    @server.tool()
    def analyze_products(product_ids: list[str]) -> dict:
        """Create proposals for 1 to 20 owned product IDs. This never changes store products."""
        if not 1 <= len(product_ids) <= 20 or any(not isinstance(p, str) or not p for p in product_ids):
            raise ValueError('Provide 1 to 20 nonempty product IDs.')
        return request('POST', '/api/runs', {'product_ids': product_ids})

    @server.tool()
    def list_proposals() -> list[dict]:
        """Read proposals and approval states. A user must approve changes in the browser."""
        return request('GET', '/api/proposals')

    @server.tool()
    def validate_approved_proposal(proposal_id: str) -> dict:
        """Dry-run validation of an approved proposal. Never applies local or Shopify mutations."""
        if not proposal_id or not all(c.isalnum() or c in '-_' for c in proposal_id):
            raise ValueError('Provide a valid proposal ID.')
        return request('POST', f'/api/proposals/{proposal_id}/execute',
                       {'dry_run': True, 'idempotency_key': 'mcp-validation-' + uuid.uuid4().hex})

    return server


if __name__ == '__main__':
    load_dotenv()
    make_server().run(transport='stdio')
