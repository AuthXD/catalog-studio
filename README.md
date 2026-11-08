# Catalog Studio

Catalog Studio helps store operators review changes before they reach a product catalog. It drafts descriptions, proposes product-specific discounts from recent price observations, and flags low stock for review. Each proposal shows the original value, the proposed change, and the evidence behind it.

## Try it locally

Use Python 3.14. From the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn catalog_app.main:app --host 127.0.0.1 --port 8023
```

Create a workspace with a password of at least 10 characters. It comes with four sample products and labeled sample prices. The local demo uses a deterministic description formatter and needs no API credentials. [Open Catalog Studio](http://127.0.0.1:8023).

1. Select products and click **Analyze catalog**.
2. Review a proposal and approve or reject it.
3. Use **Dry run** to check whether the approved proposal can still apply.
4. Apply the change and check the outcome in **Run history**.

## How changes are controlled

A LangGraph workflow creates proposals. FastAPI handles account access and decisions, and SQLite stores product versions, proposals, operation keys, and the audit trail. Applying a proposal requires a separate action after approval. Changes to the product or its price evidence invalidate proposals based on the earlier version. Retrying an operation key returns the recorded result.

Price comparisons use matching currencies and observations from the last seven days. Restock proposals record a recommendation. The Shopify adapter supports description updates and product-specific discounts. After an ambiguous remote write, the operator checks the store and records the outcome in **Run history**.

## Connect a model or a store

Copy `.env.example` to `.env`. To enable model drafts, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY`. The app records token usage per run, caps inputs and outputs, and keeps model drafts in the review queue.

To import a development store, set `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ACCESS_TOKEN`, and a supported `SHOPIFY_API_VERSION`, then open **Connections & mode**. Imports use read-product access and load up to 50 products. Set `SHOPIFY_WRITES_ENABLED=true` to enable live execution, with write scopes for the operations you intend to use. The adapter checks Shopify's product timestamp before a write. A concurrent edit can still occur between that check and the mutation.

The MCP server exposes authenticated reads, proposal generation, and dry runs through stdio. The browser handles approval and execution. [MCP setup](docs/mcp.md).

## Verify behavior

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

The 22 tests cover account isolation, approval, dry runs, stale versions, duplicate requests, provider failures, reconciliation, and MCP discovery and calls. OpenAI and Shopify request contracts were tested offline. Live account behavior still needs verification against a development store and model account.

SQLite data lives in `data/catalog.sqlite3` by default. Set `PROJECT23_DB` to choose a different file. FastAPI serves the request schemas at [localhost API docs](http://127.0.0.1:8023/docs).
