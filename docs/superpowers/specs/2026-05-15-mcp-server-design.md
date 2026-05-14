# MCP Server Design

## Goal

Expose Belgrade OS app tools to external AI agents (ChatGPT, Claude Desktop, claude.ai) via the Model Context Protocol. Agents authenticate with Cloudflare Access service tokens, discover available tools, and invoke them on behalf of the authenticated user.

## Architecture

```
ChatGPT / Claude Desktop
      │  HTTPS  beg-os.fyi/mcp/*  and  beg-os.fyi/oauth/*
      ▼
Cloudflare Edge
  ├─ /mcp/*    → CF Access: accept service tokens, passthrough to MCP Server
  ├─ /oauth/*  → CF Access: accept service tokens, passthrough to MCP Server
  └─ /*        → CF Access: email OTP (unchanged) → Gateway
      ↓
cloudflared → MCP Server (port 8083)
                  │
                  │ HTTP (internal)
                  ▼
            Bridge :8081
              GET /v1/tools?mcp=true
              POST /v1/execute
```

New `mcp_server/` Python/FastAPI service. Sits behind the same Cloudflare tunnel as the rest of the platform. CF handles TLS, brute-force protection, and credential lifecycle. The Bridge remains the single source of truth for tool definitions and execution.

## Authentication

**Credentials:** Cloudflare Access service tokens. One token per agent (e.g. "Claude Desktop", "ChatGPT"). Created in Cloudflare Zero Trust dashboard → Access → Service Auth → Service Tokens. No new service, no new billing — same CF account already in use.

**Token exchange flow:**

```
1. Agent sends:
   POST /oauth/token
   client_id=<CF-Access-Client-Id>
   client_secret=<CF-Access-Client-Secret>
   grant_type=client_credentials

2. Cloudflare validates the service token at the edge,
   injects CF-Access-Jwt-Assertion header

3. MCP server validates the CF JWT (same logic as Gateway)

4. MCP server issues a short-lived Belgrade JWT:
   { user_id, tenant_id, exp: now+1h }
   signed with MCP_JWT_SECRET

5. Returns:
   { "access_token": "...", "token_type": "bearer", "expires_in": 3600 }

6. Agent uses Bearer token for all subsequent /mcp calls
```

`user_id` and `tenant_id` are taken from `MCP_DEFAULT_USER_ID` and `MCP_DEFAULT_TENANT_ID` env vars (single-user). All service tokens map to this user. Multi-user mapping (service token → user) is out of scope.

CF handles: credential rotation, revocation, audit logs, brute-force protection.
MCP server handles: CF JWT validation, session JWT issuance and validation.

No Postgres table. No secret hashing. No credential management endpoints.

## Tool Registration Changes

Four small changes to existing files.

### manifest.json

New optional field:
```json
{ "mcp": true }
```

When present and `true`, all tools registered by this app are exposed via MCP.

### platform_controller/main.py

When launching an app whose manifest has `"mcp": true`, inject alongside existing env vars:
```
BEG_OS_MCP_ENABLED=true
```

### sdk/belgrade_sdk/app.py

`@app.tool()` gains an optional `mcp_hint` parameter:
```python
@app.tool("add-item", description="Add item to shopping list", mcp_hint="Use when user wants to buy something")
```

`RegisterRequest` gains `mcp: bool = False`. SDK reads `BEG_OS_MCP_ENABLED` at startup and sets it:
```python
mcp=os.getenv("BEG_OS_MCP_ENABLED") == "true"
```

`ToolDefinition` gains `mcp_hint: Optional[str] = None`.

### bridge/src/

`RegisterRequest` gains `mcp: bool`. `ToolDefinition` gains `mcp_hint: Option<String>`. Both stored in Redis alongside existing tool data.

New query param on the existing tools endpoint:
```
GET /v1/tools?mcp=true   → only tools from apps registered with mcp=true
GET /v1/tools            → all tools (existing behaviour preserved)
```

## MCP Protocol

Transport: Streamable HTTP (MCP's current standard for remote servers). Single `POST /mcp` endpoint, responses streamed via SSE for long-running tool calls. All requests require `Authorization: Bearer <token>`.

**`initialize`** — capability negotiation:
```json
← { "protocolVersion": "2024-11-05", "capabilities": { "tools": {} } }
```

**`tools/list`** — tool discovery. Fetches from Bridge `GET /v1/tools?mcp=true` on each request (no local cache):
```json
← {
    "tools": [
      {
        "name": "shopping:add-item",
        "description": "Add item to shopping list. Use when user wants to buy something.",
        "inputSchema": { "type": "object", "properties": { "item": { "type": "string" } } }
      }
    ]
  }
```

`mcp_hint` is appended to `description` if present. No separate field in the MCP response.

**`tools/call`** — tool execution. MCP server first verifies the tool name is present in the current `GET /v1/tools?mcp=true` response (prevents calling non-MCP-exposed tools by name directly). Then forwards to Bridge `POST /v1/execute`, generating `call_id`, `task_id`, and `trace_id` as UUIDs, injecting `user_id` and `tenant_id` from the Bearer JWT:
```json
→ { "name": "shopping:add-item", "arguments": { "item": "milk" } }

Bridge /v1/execute receives:
{ "call_id": "<uuid>", "task_id": "<uuid>", "trace_id": "<uuid>",
  "tool_name": "shopping:add-item", "input_json": "{\"item\": \"milk\"}",
  "user_id": "ivan", "tenant_id": "default" }

← { "content": [{ "type": "text", "text": "<tool result>" }] }
```

**Error handling:**
- Tool not in MCP-exposed set → `{"error": {"code": -32602, "message": "tool not found"}}`
- Bridge returns `success: false` → propagate `error` field as JSON-RPC error
- Bridge unreachable → `{"error": {"code": -32603, "message": "internal error"}}`
- Invalid or expired token → HTTP 401 before JSON-RPC layer

## New Service: mcp_server/

```
mcp_server/
├── main.py          — FastAPI app, MCP JSON-RPC handler (initialize, tools/list, tools/call)
├── oauth.py         — POST /oauth/token, CF JWT validation, Belgrade JWT issuance/validation
├── registry.py      — Bridge proxy: fetch MCP tools, route tool calls to /v1/execute
├── requirements.txt
└── tests/
    └── test_mcp.py
```

**Key env vars:**

| Var | Notes |
|---|---|
| `BRIDGE_URL` | `http://localhost:8081` |
| `MCP_JWT_SECRET` | Signs and validates session JWTs |
| `MCP_DEFAULT_USER_ID` | Belgrade OS user_id for all service tokens |
| `MCP_DEFAULT_TENANT_ID` | Belgrade OS tenant_id |
| `CF_TEAM_DOMAIN` | Same as Gateway — for CF JWT validation |
| `CF_MCP_AUDIENCE` | CF Access audience tag for the MCP policy |

## Testing

**`mcp_server/tests/test_mcp.py`:**
- Valid CF JWT → issues Belgrade JWT with correct `user_id`/`tenant_id`
- Missing or invalid CF JWT → 401
- `tools/list`: mocked Bridge → returns MCP-exposed tools with hints appended to descriptions
- `tools/call`: mocked Bridge execute → correct `user_id`/`tenant_id` injected, result in MCP format
- `tools/call` unknown tool → JSON-RPC error
- Unauthenticated `/mcp` request → 401

**`bridge/` (Rust):**
- `GET /v1/tools?mcp=true` returns only tools from `mcp=true` apps
- `GET /v1/tools` (no filter) still returns all tools

**`sdk/tests/`:**
- `BEG_OS_MCP_ENABLED=true` → `RegisterRequest.mcp == True`
- `mcp_hint` in decorator → present in `ToolDefinition`

**`platform_controller/tests/`:**
- App with `"mcp": true` in manifest → `BEG_OS_MCP_ENABLED=true` in injected env

## Out of Scope

- Per-tool MCP scopes on the JWT
- Credential revocation via API (done in CF dashboard)
- Multi-user service token mapping
- MCP sampling / resource endpoints (tools only)
- Admin UI for managing service tokens
