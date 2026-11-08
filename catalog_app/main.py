"""Same-origin API and browser application."""
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import providers
from .service import Catalog, Problem

load_dotenv()
BASE = Path(__file__).resolve().parent


class Account(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default='Catalog owner', min_length=1, max_length=60)

    @field_validator('email')
    @classmethod
    def email_format(cls, value):
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Enter a valid email address.')
        return value.lower()


class Product(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=6000)
    price: float = Field(gt=0, le=100000, allow_inf_nan=False)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    inventory: int = Field(ge=0, le=1000000)
    expected_version: int | None = Field(default=None, ge=1)


class Evidence(BaseModel):
    source: str = Field(min_length=3, max_length=500)
    price: float = Field(gt=0, le=100000, allow_inf_nan=False)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    observed_at: datetime

    @field_validator('observed_at')
    @classmethod
    def observed(cls, value):
        if value.tzinfo is None:
            raise ValueError('Include a timezone in the observed time.')
        if value > datetime.now(timezone.utc):
            raise ValueError('Observation cannot be in the future.')
        return value


class Analyze(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=20)


class Decision(BaseModel):
    decision: str = Field(pattern=r'^(approved|rejected)$')


class Execute(BaseModel):
    dry_run: bool = True
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class Reconcile(BaseModel):
    confirmed_applied: bool
    note: str = Field(min_length=10, max_length=1000)


def create_app(db_path=None):
    app = FastAPI(title='Catalog Studio', version='1.0.0')
    store = Catalog(db_path or os.getenv('PROJECT23_DB', str(BASE.parent / 'data/catalog.sqlite3')))
    app.state.catalog = store
    login_attempts = defaultdict(deque)

    @app.exception_handler(Problem)
    async def problem_handler(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=exc.status)

    @app.exception_handler(providers.ProviderError)
    async def provider_handler(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=502)

    @app.middleware('http')
    async def security(request: Request, call_next):
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'Cross-origin requests are blocked.'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Cross-site requests are blocked.'}, status_code=403)
            try:
                if int(request.headers.get('content-length', '0')) > 200000:
                    return JSONResponse({'detail': 'Request is too large.'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length.'}, status_code=400)
            if request.url.path in {'/api/auth/login', '/api/auth/register'}:
                key = request.client.host if request.client else 'local'
                attempts = login_attempts[key]
                while attempts and attempts[0] < time.monotonic() - 60:
                    attempts.popleft()
                if len(attempts) >= 15:
                    return JSONResponse({'detail': 'Too many attempts. Try again in a minute.'}, status_code=429)
                attempts.append(time.monotonic())
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def current(request: Request):
        return store.user(request.cookies.get('catalog_session'))

    def session_response(value):
        token, user = value
        response = JSONResponse(user)
        response.set_cookie('catalog_session', token, httponly=True, samesite='strict',
                            secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', max_age=86400)
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok', 'application': 'catalog-studio'}

    @app.get('/api/config')
    def config():
        return {'mode': providers.mode(), 'version': '1.0.0', 'price_evidence_max_age_days': 7}

    @app.post('/api/auth/register')
    def register(data: Account):
        return session_response(store.register(data.email, data.name.strip(), data.password))

    @app.post('/api/auth/login')
    def login(data: Account):
        return session_response(store.login(data.email, data.password))

    @app.post('/api/auth/logout')
    def logout(request: Request):
        store.logout(request.cookies.get('catalog_session'))
        response = JSONResponse({'ok': True})
        response.delete_cookie('catalog_session')
        return response

    @app.get('/api/me')
    def me(user=Depends(current)):
        return user

    @app.get('/api/products')
    def products(user=Depends(current)):
        return store.catalog(user['id'])

    @app.post('/api/products')
    def add_product(data: Product, user=Depends(current)):
        return store.save_product(user['id'], data.model_dump(exclude={'expected_version'}))

    @app.put('/api/products/{ident}')
    def edit_product(ident: str, data: Product, user=Depends(current)):
        return store.save_product(user['id'], data.model_dump(exclude={'expected_version'}), ident, data.expected_version)

    @app.post('/api/products/{ident}/evidence')
    def evidence(ident: str, data: Evidence, user=Depends(current)):
        values = data.model_dump()
        values['observed_at'] = data.observed_at.isoformat()
        store.add_evidence(user['id'], ident, values)
        return {'ok': True}

    @app.post('/api/shopify/import')
    def import_shopify(user=Depends(current)):
        incoming = providers.import_shopify()
        current_products = {p['remote_id']: p for p in store.catalog(user['id']) if p['remote_id']}
        for item in incoming:
            # Validate API output before persistence.
            product = Product(**item).model_dump(exclude={'expected_version'})
            old = current_products.get(item['remote_id'])
            if old:
                store.save_product(user['id'], product, old['id'], old['version'])
                with store.db() as db:
                    db.execute('UPDATE products SET remote_version=? WHERE id=?', (item['remote_version'], old['id']))
            else:
                store.save_product(user['id'], {**product, 'origin': 'shopify', 'remote_id': item['remote_id'], 'remote_version': item['remote_version']})
        return {'imported': len(incoming)}

    @app.post('/api/runs')
    def run(data: Analyze, user=Depends(current)):
        return store.analyze(user['id'], data.product_ids)

    @app.get('/api/proposals')
    def proposals(user=Depends(current)):
        return store.proposals(user['id'])

    @app.post('/api/proposals/{ident}/decision')
    def decide(ident: str, data: Decision, user=Depends(current)):
        return store.decide(user['id'], ident, data.decision)

    @app.post('/api/proposals/{ident}/execute')
    def execute(ident: str, data: Execute, user=Depends(current)):
        return store.execute(user['id'], ident, data.idempotency_key, data.dry_run)

    @app.get('/api/history')
    def history(user=Depends(current)):
        return store.history(user['id'])

    @app.post('/api/operations/{ident}/reconcile')
    def reconcile(ident: str, data: Reconcile, user=Depends(current)):
        return store.reconcile(user['id'], ident, data.confirmed_applied, data.note)

    @app.get('/api/export')
    def export(user=Depends(current)):
        data = {'products': store.catalog(user['id']), 'proposals': store.proposals(user['id']), 'history': store.history(user['id'])}
        return Response(json.dumps(data, indent=2), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="catalog-studio-export.json"'})

    app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')

    @app.get('/')
    def home():
        return FileResponse(BASE / 'static/index.html')

    return app


app = create_app()
