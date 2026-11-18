# MCP Connectors via Cloudflare Managed OAuth — Design

**Date:** 2026-11-18 · **Status:** draft for approval

## Intent

Today the shopping list (and every other Belgrade OS tool) is reachable only from Claude Desktop on Ivan's Mac, through a local stdio proxy (`tools/mcp_proxy/`) that adds a Cloudflare Access service token. Phone apps can't run that proxy.

Goal: make `https://mcp.beg-os.fyi/mcp` a **standard remote MCP connector** that any MCP-capable app can add with an OAuth sign-in. **Primary target: Claude mobile** (Ivan has Claude Pro; custom connectors added on claude.ai are usable in the iOS/Android apps, write tools included). **Secondary: ChatGPT** (Ivan has the Go plan; whether Go allows write-capable custom MCP connectors is unconfirmed — checked at the end, not a blocker). Add once on the app's website, sign in with the existing Cloudflare email one-time code, then use it from the phone.

**Success looks like:** on his phone, Ivan tells **Claude** "add eggs to the shopping list", and it appears in the same `family` list Claude Desktop sees, with `added_by` = his email. ChatGPT working the same way is a bonus; if Go can't write via custom connectors, record that and stop there.

## Users

Ivan now; maybe family members later. Each person signs in as themselves; who may sign in is controlled **only** by the Cloudflare Access policy (an email allow-list). Everyone shares tenant `family`. No per-person permissions inside Belgrade OS.

## Goals

1. OAuth sign-in for remote MCP clients (Claude mobile first, ChatGPT second), provided by **Cloudflare Access Managed OAuth** (Ivan's decision: "Cloudflare, keep it simple").
2. The MCP server identifies the caller from the **Cloudflare Access JWT** (`Cf-Access-Jwt-Assertion`) on every request; user = the JWT's `email`.
3. Claude Desktop via the existing proxy + service token keeps working throughout.
4. The MCP endpoint speaks enough of the current MCP Streamable HTTP transport that ChatGPT and claude.ai connect cleanly.

## Non-goals

- Writing our own OAuth authorization server (explicitly rejected — Cloudflare does registration, login, tokens, refresh).
- Per-person tool permissions or RBAC; per-person tenants.
- New tools or changes to the shopping app.
- Retiring the desktop proxy / service token (possible follow-up once OAuth works in Claude Desktop natively — see "Follow-ups").

## Anti-goals (failure even if it "works")

- Any path where a request reaches the MCP tools **without a valid Access JWT validated at the origin** — Cloudflare's docs require the origin to validate it; edge config alone is not trusted (defense in depth, same principle as C1).
- Breaking Claude Desktop for the household before the phone path is proven.
- Opening the endpoint to anyone outside the Access allow-list (e.g. a policy that allows "everyone" or any OTP email).

## Current state (what exists)

- `services/mcp_server/main.py`: `POST /mcp` (JSON-RPC: `initialize`, `tools/list`, `tools/call`) requires `Authorization: Bearer <Belgrade HS256 JWT>`. `POST /oauth/token` exchanges a CF Access JWT (service token) for that Belgrade JWT (`client_credentials`).
- `services/mcp_server/oauth.py`: `validate_cf_jwt()` already validates Access JWTs (RS256, JWKS from `https://${CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs`, audience `CF_MCP_AUDIENCE`).
- `tools/mcp_proxy/`: sends `CF-Access-Client-Id/Secret` on every request, calls `/oauth/token`, then `/mcp` with the Belgrade bearer.
- Cloudflare: Access self-hosted app on `mcp.beg-os.fyi` with a **Service Auth** policy only; tunnel route `mcp.beg-os.fyi → :8083`. Team login uses email one-time PIN.
- `initialize` answers `protocolVersion: "2024-11-05"`; any other method (including notifications such as `notifications/initialized`) gets a JSON-RPC "method not found" error.

## Design

### 1. Cloudflare (dashboard/API, done by Ivan or with him)

- On the existing `mcp.beg-os.fyi` Access application: **enable Managed OAuth** (Advanced settings → Managed OAuth, or API `oauth_configuration.enabled: true` — PUT must include all fields from the GET).
- Add an **Allow** policy: identity = email one-time PIN, include = explicit email list (Ivan's email; family added later). Keep the existing **Service Auth** policy.
- Dynamic client registration: **allowed redirect URIs** = the ChatGPT and claude.ai OAuth callbacks (claude.ai: `https://claude.ai/api/mcp/auth_callback`; ChatGPT's to be confirmed from OpenAI docs / first connection attempt). Localhost/loopback clients off.
- Token lifetimes: access token 15 min (default), grant session ~2 weeks (Cloudflare's recommendation for agent clients), so the phone doesn't ask to sign in constantly.

### 2. MCP server: authenticate by Access JWT (`services/mcp_server/`)

- `/mcp` authenticates with the **`Cf-Access-Jwt-Assertion` header**, validated by the existing `validate_cf_jwt()` (signature, RS256, audience, expiry). Missing/invalid → 401.
- The `Authorization` header is **ignored** by `/mcp`. (With Managed OAuth it carries Cloudflare's opaque `oauth:…` token, which the origin can't and shouldn't validate; with the proxy it carries the Belgrade JWT, which is now redundant.) The server must **not** emit its own `WWW-Authenticate` challenges — Cloudflare owns the 401/discovery behavior.
- Identity mapping:
  - User login (JWT has `email`): `user_id = email`.
  - Service token (no `email`; has `common_name`): `user_id = MCP_DEFAULT_USER_ID`.
  - `tenant_id = MCP_DEFAULT_TENANT_ID` (`family`) for everyone.
  - *(Claude's call)* **One identity per person across clients:** set the Lenovo's `MCP_DEFAULT_USER_ID` (key present but currently **empty**) to the same email Ivan signs in to Access with, so Desktop and phone write the same `added_by` and anything the SDK keys on `user_id` (e.g. `schedule_id`) doesn't split. Existing rows: only one item with blank `added_by` — no backfill.
- `/oauth/token` stays as-is for now so the unchanged proxy keeps working (it still calls it and sends the bearer, which `/mcp` now ignores).
- Startup: `CF_TEAM_DOMAIN` and `CF_MCP_AUDIENCE` become required for `/mcp` (they already are in production).

*Why the origin validates the JWT (standard practice):* Cloudflare Access is a reverse proxy; if a request ever reaches the origin around it (misconfigured route, a second hostname, a LAN/tunnel mistake), only origin-side validation stops it. Cloudflare's own docs say to enable Managed OAuth only for servers that validate the Access JWT.

### 3. MCP protocol compliance for remote clients

Bring `/mcp` up to the Streamable HTTP transport as remote clients expect:
- `initialize`: negotiate `protocolVersion` — echo the client's requested version if we support it, else answer with our latest supported (at least `2025-06-18`; keep `2024-11-05` for the proxy).
- JSON-RPC **notifications** (no `id`, e.g. `notifications/initialized`): respond **202 Accepted**, no body.
- `GET /mcp` (server→client SSE stream, which we don't offer): **405 Method Not Allowed**.
- `ping`: return `{}`.
- Responses stay `application/json` (allowed by the spec); no SSE needed.

Exact required behavior to be confirmed against the current MCP spec and a live connect from ChatGPT and claude.ai.

*(Claude's call)* **The proxy may change, and ships with the server change:** `tools/mcp_proxy` currently treats any non-200 as an error, so the 202 for notifications would log spurious errors in Claude Desktop. Fix the proxy to accept 202 (drop notifications silently). The server must **not require** the `MCP-Protocol-Version` header, so older/unchanged clients keep working regardless of the negotiated version.

## Rollout

1. Ship the server changes (2, 3) and the proxy fix with tests; set `MCP_DEFAULT_USER_ID` to Ivan's Access email; deploy to the Lenovo; confirm Claude Desktop still works (it should — the proxy's service-token request now authenticates by its Access JWT).
2. Cloudflare: add the email Allow policy, enable Managed OAuth, set redirect URIs.
3. Verify Claude Desktop again (service token still accepted with Managed OAuth on).
4. Add the connector on **claude.ai** → sign in with email code → test "add eggs" in Claude mobile. **This is done-done.**
5. Then try chatgpt.com (Go plan): add the custom connector, sign in, test on the phone. If Go only allows read-only connectors, note it and stop — no new tools for this.
6. Record results in the Obsidian security note (M4 partially addressed: per-person identity for OAuth users).

**Rollback:** turn Managed OAuth off and remove the email policy in Cloudflare; the server change is backward compatible with the proxy.

## Risks / unknowns (to verify, not to assume)

- **Service token + Managed OAuth on one Access app**: Cloudflare's docs don't say both work together. If the service-token path breaks, fall back to a second Access app/hostname for the proxy (e.g. `mcp-desktop.beg-os.fyi`) — same origin, same JWT validation, different audience (`CF_MCP_AUDIENCE` becomes a list).
- **Protected Resource Metadata** (`/.well-known/oauth-protected-resource`, RFC 9728): Cloudflare says its discovery conforms to RFC 9728 and its 401 points clients to its metadata; whether the origin must serve anything is not stated. Verify with a live client; serve it from the origin only if needed.
- **Client compatibility**: Cloudflare doesn't name ChatGPT/claude.ai as tested clients; ChatGPT may prefer CIMD over DCR. Requires a live test; GitHub reports show claude.ai DCR failures with some setups.
- **Access JWT claims**: confirm on the real token that user logins carry `email` and service tokens carry `common_name` and no `email`.

## Testing

- Unit (`services/mcp_server/tests/`): `/mcp` with valid user JWT → `user_id = email`; service-token JWT → default user; missing/invalid/expired/wrong-audience JWT → 401; `Authorization` header alone (Belgrade JWT or `oauth:…`) → 401 without a valid Access JWT; no `WWW-Authenticate` header emitted; notifications → 202; `GET /mcp` → 405; version negotiation. Use a locally generated RSA key and a stubbed JWKS client.
- Existing `tools/mcp_proxy` tests keep passing unchanged.
- Live: Claude Desktop before/after each Cloudflare change; ChatGPT web + phone; claude.ai + Claude mobile.

## Follow-ups (not in this work)

- Once OAuth works, Claude Desktop can use the same remote connector natively → retire `tools/mcp_proxy`, the service token, `/oauth/token`, and `MCP_JWT_SECRET`.
- Per-person tool permissions if family members get access to more than the shared lists.

## Left to the builder

- Module layout of the auth change inside `mcp_server`, test fixtures, error message wording.
- Exact protocol versions supported, per the current MCP spec.
