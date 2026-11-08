"""Catalog operations, ownership and transaction invariants live together."""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import providers


def now():
    return datetime.now(timezone.utc).isoformat()


class Problem(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def uid():
    return uuid.uuid4().hex


class AnalysisState(TypedDict):
    product: dict
    evidence: list[dict]
    proposals: list[dict]
    usage: dict


def propose_description(state):
    generated = providers.description(state['product'])
    proposal = {'kind': 'description', 'before': state['product']['description'],
                'after': generated['text'], 'reason': 'Review a clearer description based on supplied product facts.',
                'evidence': [], 'discount_pct': 0}
    return {'proposals': [proposal], 'usage': generated}


def propose_actions(state):
    product = state['product']
    proposals = list(state['proposals'])
    valid = [e for e in state['evidence'] if e['currency'] == product['currency'] and e['price'] > 0]
    if valid:
        avg = sum(e['price'] for e in valid) / len(valid)
        if product['price'] > avg * 1.1:
            proposals.append({'kind': 'promotion', 'before': f"{product['currency']} {product['price']:.2f}",
                'after': f"Product-only 10% offer: {product['currency']} {product['price'] * .9:.2f}",
                'reason': f"Listed price is {(product['price'] / avg - 1) * 100:.1f}% above the observed comparison average. Verify the item match before approving.",
                'evidence': valid, 'discount_pct': 10})
    if product['inventory'] < 10:
        proposals.append({'kind': 'restock', 'before': f"{product['inventory']} units recorded",
            'after': 'Review replenishment with your supplier. Physical stock will not be changed.',
            'reason': 'Recorded inventory is below 10 units.', 'evidence': [], 'discount_pct': 0})
    return {'proposals': proposals}


graph = StateGraph(AnalysisState)
graph.add_node('description', propose_description)
graph.add_node('policy', propose_actions)
graph.set_entry_point('description')
graph.add_edge('description', 'policy')
graph.add_edge('policy', END)
analysis_graph = graph.compile()


class Catalog:
    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,owner TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,
              price REAL NOT NULL,currency TEXT NOT NULL,inventory INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1,
              origin TEXT NOT NULL DEFAULT 'demo',remote_id TEXT,remote_version TEXT,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,product TEXT NOT NULL,source TEXT NOT NULL,price REAL NOT NULL,currency TEXT NOT NULL,observed_at TEXT NOT NULL,is_sample INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,usage TEXT NOT NULL,error TEXT);
            CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,owner TEXT NOT NULL,run TEXT NOT NULL,product TEXT NOT NULL,version INTEGER NOT NULL,
              kind TEXT NOT NULL,before_text TEXT NOT NULL,after_text TEXT NOT NULL,reason TEXT NOT NULL,evidence TEXT NOT NULL,
              discount_pct REAL NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,owner TEXT NOT NULL,proposal TEXT NOT NULL,key TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(owner,key));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT NOT NULL,event TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, owner, event, detail):
        db.execute('INSERT INTO audit(owner,event,detail,created_at) VALUES(?,?,?,?)',
                   (owner, event, json.dumps(detail), now()))

    @staticmethod
    def password(raw, salt=None):
        salt = salt or secrets.token_hex(16)
        return salt + ':' + hashlib.pbkdf2_hmac('sha256', raw.encode(), salt.encode(), 310000).hex()

    def register(self, email, name, password):
        owner = uid()
        try:
            with self.db() as db:
                db.execute('INSERT INTO users VALUES(?,?,?,?)', (owner, email.lower(), name, self.password(password)))
        except sqlite3.IntegrityError:
            raise Problem('An account with this email already exists.', 409)
        self.seed(owner)
        return self.login(email, password)

    def login(self, email, password):
        with self.db() as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email.lower(),)).fetchone()
            hashed = self.password(password, row['password'].split(':')[0] if row else 'unknown')
            if not row or not hmac.compare_digest(hashed, row['password']):
                raise Problem('Email or password is incorrect.', 401)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time() + 86400))
            return token, {'id': row['id'], 'name': row['name'], 'email': row['email']}

    def user(self, token):
        if not token:
            raise Problem('Sign in to continue.', 401)
        with self.db() as db:
            row = db.execute('SELECT u.id,u.name,u.email FROM users u JOIN sessions s ON s.owner=u.id WHERE s.token=? AND s.expires>?',
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise Problem('Your session expired. Sign in again.', 401)
            return dict(row)

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256((token or '').encode()).hexdigest(),))

    def seed(self, owner):
        products = [('Field tote', 'Heavy cotton canvas tote with two interior pockets. Dimensions: 38 x 34 cm. Natural cotton color.', 48, 7),
                    ('Stoneware mug', 'Hand-finished ceramic mug. Capacity: 350 ml. Dishwasher safe. Sand glaze.', 28, 24),
                    ('Task notebook', 'A5 notebook with 160 dotted pages. Recycled paper, lay-flat binding, charcoal cover.', 22, 5),
                    ('Desk light', 'Adjustable LED desk lamp. Three brightness levels, USB-C cable included. Matte black finish.', 85, 18)]
        for index, (title, desc, price, inventory) in enumerate(products):
            product = self.save_product(owner, {'title': title, 'description': desc, 'price': price,
                'currency': 'USD', 'inventory': inventory})
            self.add_evidence(owner, product['id'], {'source': 'Bundled sample comparison; illustrative, not market data',
                'price': round(price * (.78 if index in (0, 2) else 1.02), 2), 'currency': 'USD',
                'observed_at': now(), 'is_sample': True})

    def owned(self, db, table, owner, ident):
        if table not in {'products', 'proposals', 'runs'}:
            raise ValueError('Unknown table')
        row = db.execute(f'SELECT * FROM {table} WHERE id=? AND owner=?', (ident, owner)).fetchone()
        if not row:
            raise Problem('Record not found.', 404)
        return dict(row)

    def save_product(self, owner, data, ident=None, expected_version=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if ident:
                current = self.owned(db, 'products', owner, ident)
                if db.execute("SELECT 1 FROM proposals WHERE product=? AND status='executing'", (ident,)).fetchone():
                    raise Problem('An execution is in progress for this product.', 409)
                if expected_version != current['version']:
                    raise Problem('Product changed. Refresh and try again.', 409)
                db.execute('UPDATE products SET title=?,description=?,price=?,currency=?,inventory=?,version=version+1,updated_at=? WHERE id=?',
                    (data['title'], data['description'], data['price'], data['currency'], data['inventory'], now(), ident))
            else:
                ident = uid()
                db.execute('INSERT INTO products(id,owner,title,description,price,currency,inventory,origin,remote_id,remote_version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, owner, data['title'], data['description'], data['price'], data['currency'], data['inventory'],
                     data.get('origin', 'demo'), data.get('remote_id'), data.get('remote_version'), now()))
            self.audit(db, owner, 'product_saved', {'product': ident})
            return self.owned(db, 'products', owner, ident)

    def add_evidence(self, owner, ident, data):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.owned(db, 'products', owner, ident)
            if db.execute("SELECT 1 FROM proposals WHERE product=? AND status='executing'", (ident,)).fetchone():
                raise Problem('An execution is in progress for this product.', 409)
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)', (uid(), ident, data['source'], data['price'],
                data['currency'], data['observed_at'], int(data.get('is_sample', False))))
            db.execute('UPDATE products SET version=version+1,updated_at=? WHERE id=?', (now(), ident))
            self.audit(db, owner, 'price_evidence_added', {'product': ident, 'source': data['source']})

    def catalog(self, owner):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM products WHERE owner=? ORDER BY title', (owner,))]
            for p in rows:
                p['evidence'] = [dict(r) for r in db.execute('SELECT * FROM evidence WHERE product=? ORDER BY observed_at DESC', (p['id'],))]
            return rows

    def analyze(self, owner, ids):
        if not ids or len(ids) > 20:
            raise Problem('Select between 1 and 20 products.')
        run_id = uid()
        with self.db() as db:
            products = [self.owned(db, 'products', owner, ident) for ident in dict.fromkeys(ids)]
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (run_id, owner, 'running', now(), '{}', None))
        totals = {'input_tokens': 0, 'output_tokens': 0, 'provider': providers.mode()['llm']}
        try:
            for p in products:
                with self.db() as db:
                    evidence = [dict(r) for r in db.execute('SELECT * FROM evidence WHERE product=?', (p['id'],))]
                # Stale observations are not used to recommend offers.
                evidence = [e for e in evidence if (datetime.now(timezone.utc) - datetime.fromisoformat(e['observed_at'])).total_seconds() <= 7 * 86400]
                result = analysis_graph.invoke({'product': p, 'evidence': evidence, 'proposals': [], 'usage': {}})
                totals['input_tokens'] += result['usage']['input_tokens']
                totals['output_tokens'] += result['usage']['output_tokens']
                if totals['input_tokens'] + totals['output_tokens'] > int(os.getenv('RUN_TOKEN_LIMIT', '20000')):
                    raise providers.ProviderError('Run token limit reached. Remaining products were not analyzed.')
                with self.db() as db:
                    for proposal in result['proposals']:
                        db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (uid(), owner, run_id, p['id'], p['version'], proposal['kind'], proposal['before'], proposal['after'],
                             proposal['reason'], json.dumps(proposal['evidence']), proposal['discount_pct'], 'proposed', now()))
            status, error = 'completed', None
        except Exception as exc:
            status, error = 'failed', str(exc) if isinstance(exc, providers.ProviderError) else 'Analysis failed. Check the server log and retry.'
        with self.db() as db:
            db.execute('UPDATE runs SET status=?,usage=?,error=? WHERE id=?', (status, json.dumps(totals), error, run_id))
            self.audit(db, owner, 'analysis_' + status, {'run': run_id, 'error': error})
        return {'id': run_id, 'status': status, 'error': error, 'usage': totals}

    def proposals(self, owner):
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT p.*,c.title FROM proposals p JOIN products c ON c.id=p.product WHERE p.owner=? ORDER BY p.created_at DESC', (owner,))]
            for row in rows:
                row['evidence'] = json.loads(row['evidence'])
            return rows

    def decide(self, owner, ident, decision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            p = self.owned(db, 'proposals', owner, ident)
            if p['status'] != 'proposed':
                raise Problem('This proposal already has a decision.', 409)
            db.execute('UPDATE proposals SET status=? WHERE id=?', (decision, ident))
            self.audit(db, owner, 'proposal_' + decision, {'proposal': ident})
        return {'status': decision}

    def execute(self, owner, ident, key, dry_run):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            p = self.owned(db, 'proposals', owner, ident)
            product = self.owned(db, 'products', owner, p['product'])
            if dry_run:
                return {'status': 'dry_run', 'writes': 0, 'approved': p['status'] == 'approved',
                        'stale': p['version'] != product['version'], 'action': p['after_text']}
            existing = db.execute('SELECT * FROM operations WHERE owner=? AND key=?', (owner, key)).fetchone()
            if existing:
                if existing['proposal'] != ident:
                    raise Problem('This operation key belongs to another proposal.', 409)
                return {**json.loads(existing['result']), 'status': existing['status'], 'id': existing['id'], 'replayed': True}
            if p['status'] != 'approved':
                raise Problem('Approve this proposal before executing it.', 409)
            if p['version'] != product['version']:
                raise Problem('Product or evidence changed. Create a fresh analysis.', 409)
            if db.execute("SELECT 1 FROM proposals WHERE product=? AND status='executing'", (product['id'],)).fetchone():
                raise Problem('Another execution is in progress for this product.', 409)
            operation_id = uid()
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?)', (operation_id, owner, ident, key, 'executing', '{}', now()))
            db.execute('UPDATE proposals SET status=? WHERE id=?', ('executing', ident))
        outcome = {'writes': 0, 'message': ''}
        status = 'applied'
        try:
            if p['kind'] == 'restock':
                outcome['message'] = 'Restock recommendation recorded. No stock quantity changed.'
            elif product['origin'] == 'shopify':
                payload = {'kind': p['kind'], 'after': p['after_text'], 'discount_pct': p['discount_pct']}
                remote = providers.write_shopify(product, payload, operation_id)
                outcome.update(writes=1, remote_result=remote, message='Approved change sent to Shopify. Re-import before another live change.')
            else:
                outcome.update(writes=1, message='Approved change applied to your local demo catalog.')
            if p['kind'] == 'promotion':
                outcome['code'] = 'CATALOG-' + operation_id[:12].upper()
        except Exception as exc:
            # A timed-out external write can have succeeded remotely. Never automatically retry it.
            status = 'unknown' if isinstance(exc, providers.UnknownOutcomeError) else 'failed'
            outcome = {'writes': 0, 'message': str(exc) if isinstance(exc, providers.ProviderError) else 'Execution failed.',
                       'reconciliation_required': status == 'unknown'}
        with self.db() as db:
            if status == 'applied' and p['kind'] == 'description':
                db.execute('UPDATE products SET description=?,version=version+1,updated_at=? WHERE id=?',
                    (p['after_text'], now(), product['id']))
            elif status == 'applied' and p['kind'] == 'promotion':
                db.execute('UPDATE products SET version=version+1,updated_at=? WHERE id=?', (now(), product['id']))
            db.execute('UPDATE proposals SET status=? WHERE id=?', (status, ident))
            db.execute('UPDATE operations SET status=?,result=? WHERE id=?', (status, json.dumps(outcome), operation_id))
            self.audit(db, owner, 'execution_' + status, {'proposal': ident, 'operation': operation_id, **outcome})
        return {'id': operation_id, 'status': status, **outcome}

    def reconcile(self, owner, operation_id, applied, note):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            op = db.execute('SELECT * FROM operations WHERE id=? AND owner=?', (operation_id, owner)).fetchone()
            if not op:
                raise Problem('Operation not found.', 404)
            if op['status'] not in {'unknown', 'executing'}:
                raise Problem('This operation does not need reconciliation.', 409)
            if op['status'] == 'executing' and (datetime.now(timezone.utc) - datetime.fromisoformat(op['created_at'])).total_seconds() < 60:
                raise Problem('This operation may still be running. Wait at least a minute before resolving it.', 409)
            p = self.owned(db, 'proposals', owner, op['proposal'])
            status = 'applied' if applied else 'failed'
            result = {'writes': None, 'reconciled': True, 'message': note, 'confirmed_by': owner}
            if applied and p['kind'] == 'description':
                db.execute('UPDATE products SET description=?,version=version+1,updated_at=? WHERE id=?', (p['after_text'], now(), p['product']))
            db.execute('UPDATE proposals SET status=? WHERE id=?', (status, p['id']))
            db.execute('UPDATE operations SET status=?,result=? WHERE id=?', (status, json.dumps(result), operation_id))
            self.audit(db, owner, 'execution_reconciled', {'operation': operation_id, 'applied': applied, 'note': note})
            return {'status': status, **result}

    def history(self, owner):
        with self.db() as db:
            return {'runs': [dict(r) for r in db.execute('SELECT * FROM runs WHERE owner=? ORDER BY created_at DESC LIMIT 100', (owner,))],
                    'events': [dict(r) for r in db.execute('SELECT * FROM audit WHERE owner=? ORDER BY id DESC LIMIT 200', (owner,))],
                    'operations': [dict(r) for r in db.execute('SELECT * FROM operations WHERE owner=? ORDER BY created_at DESC LIMIT 100', (owner,))]}
