# Connect Catalog Studio through MCP

Catalog Studio includes a real stdio MCP server using the official Python SDK. Four tools call the authenticated local HTTP API: `list_catalog`, `analyze_products`, `list_proposals`, and `validate_approved_proposal`. The last tool always forces dry-run mode. No MCP tool can approve or apply changes.

## Start the connection

1. Start Catalog Studio at `http://127.0.0.1:8023`.
2. Create an account in the browser.
3. Add `CATALOG_MCP_EMAIL` and `CATALOG_MCP_PASSWORD` to the repository's `.env` using that local account.
4. Configure your MCP client to run Python with `-m catalog_app.mcp_server` from the repository root.

Use the absolute path to `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS or Linux. The MCP client must inherit the credentials or load them from the project's `.env`. `CATALOG_API_URL` defaults to the address above and accepts localhost addresses only. Redirects are disabled so the bridge cannot forward credentials to a redirected remote host.

The module loads environment configuration when started as a program:

```powershell
.venv\Scripts\python.exe -m catalog_app.mcp_server
```

This command speaks MCP over standard input and output. Run it through an MCP client, rather than expecting a browser page or a terminal prompt.

## Verify the protocol

`tests/test_mcp.py` covers discovery, tool calls, forced dry-run mode, local authentication, and a subprocess stdio initialization and `tools/list` exchange. Account permissions apply to every API request.

Server and client integration follow the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). No connection to the user's other apps is required.
