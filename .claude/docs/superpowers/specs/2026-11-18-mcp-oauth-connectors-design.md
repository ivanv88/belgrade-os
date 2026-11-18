# MCP Connectors via Cloudflare Managed OAuth — Design

**Date:** 2026-11-18 · **Status:** draft for approval

## Intent

Today the shopping list (and every other Belgrade OS tool) is reachable only from Claude Desktop on Ivan's Mac, through a local stdio proxy (`tools/mcp_proxy/`) that adds a Cloudflare Access service token. Phone apps can't run that proxy, and a special desktop path is extra code to maintain.

Goal: make `https://mcp.beg-os.fyi/mcp` a **standard remote MCP connector** with an OAuth sign-in, and make it **the only way in, for every client**. A connector added once on claude.ai works in Claude Desktop, on claude.ai and in the Claude iOS/Android apps. Sign-in uses the existing Cloudflare email one-time code.

- **Primary target: Claude** — mobile and Desktop (Ivan has Claude Pro; custom connectors include write tools).
- **Secondary: ChatGPT** (Ivan has the Go plan; whether Go allows write-capable custom MCP connectors is unconfirmed — checked at the end, not a blocker).

**Success looks like:** on his phone, Ivan tells Claude "add eggs to the shopping list"; it appears in the `family` list, with `added_by` = his email, and Claude Desktop sees it through the **same** connector. The proxy, service token and Belgrade-token code are gone from the repo, the Mac and Cloudflare.

## Users

Ivan now; maybe family members later. Each person signs in as themselves; who may sign in is controlled **only** by the Cloudflare Access policy (an email allow-list). Everyone shares tenant `family`. No per-person permissions inside Belgrade OS.

## Goals

1. OAuth sign-in for all MCP clients, provided by **Cloudflare Access Managed OAuth** (Ivan's decision: "Cloudflare, keep it simple").
2. One path for every client (Ivan's decision: no separate desktop path — "it's harder to maintain").
3. The MCP server identifies the caller from the **Cloudflare Access JWT** (`Cf-Access-Jwt-Assertion`) on every request; user = the JWT's `email`.
4. The MCP endpoint speaks enough of the current MCP Streamable HTTP transport that claude.ai/Claude Desktop/Claude mobile and ChatGPT connect cleanly.
5. Remove the old path completely as the last step of this work: `tools/mcp_proxy/`, `/oauth/token`, Belgrade JWT code, `MCP_JWT_SECRET`, `MCP_DEFAULT_USER_ID`, the Service Auth policy and the service token.

## Non-goals

- Writing our own OAuth authorization server (explicitly rejected — Cloudflare does registration, login, tokens, refresh).
- Per-person tool permissions or RBAC; per-person tenants.
- New tools or changes to the shopping app.

## Anti-goals (failure even if it "works")

- Any path where a request reaches the MCP tools **without a valid Access JWT validated at the origin** — Cloudflare's docs require the origin to validate it; edge config alone is not trusted (defense in depth, same principle as C1).
- Losing working shopping access mid-switch: the old desktop path is removed only **after** the new connector is proven in Claude Desktop and Claude mobile.
- Opening the endpoint to anyone outside the Access allow-list (e.g. a policy that allows "everyone" or any OTP email).
- Leaving the old path half-removed (dead config, a still-valid service token, docs describing the proxy).

## Current state (what exists)

- `services/mcp_server/main.py`: `POST /mcp` (JSON-RPC: `initialize`, `tools/list`, `tools/call`) requires `Authorization: Bearer <Belgrade HS256 JWT>`. `POST /oauth/token` exchanges a CF Access JWT (service token) for that Belgrade JWT (`client_credentials`).
- `services/mcp_server/oauth.py`: `validate_cf_jwt()` already validates Access JWTs (RS256, JWKS from `https://${CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs`, audience `CF_MCP_AUDIENCE`). `issue_token()` / `validate_token()` handle the Belgrade JWT.
- `tools/mcp_proxy/` (~400 lines incl. tests): stdio proxy for Claude Desktop; service-token credentials from macOS Keychain; calls `/oauth/token`, then `/mcp` with the Belgrade bearer.
- Cloudflare: Access self-hosted app on `mcp.beg-os.fyi` with a **Service Auth** policy only; tunnel route `mcp.beg-os.fyi → :8083`. Team login uses email one-time PIN.
- Lenovo `.env`: `MCP_DEFAULT_USER_ID` key present but **empty** (so Desktop writes blank `added_by` today).
- `initialize` answers `protocolVersion: "2024-11-05"`; any other method (including notifications such as `notifications/initialized`) gets a JSON-RPC "method not found" error.
- References to the old path outside `services/mcp_server` and `tools/mcp_proxy`: `Makefile`, `CLAUDE.md`, `scripts/setup.sh`, `docs/main.spec.md`.

## Design

### 1. Cloudflare (dashboard/API, done by Ivan or with him)

- On the existing `mcp.beg-os.fyi` Access application: **enable Managed OAuth** (Advanced settings → Managed OAuth, or API `oauth_configuration.enabled: true` — PUT must include all fields from the GET).
- Add an **Allow** policy: identity = email one-time PIN, include = explicit email list (Ivan's email; family added later).
- Dynamic client registration: **allowed redirect URIs** = the claude.ai callback (`https://claude.ai/api/mcp/auth_callback`) and ChatGPT's (to be confirmed from OpenAI docs / first connection attempt). Localhost/loopback clients off.
- Token lifetimes: access token 15 min (default), grant session ~2 weeks (Cloudflare's recommendation for agent clients), so clients don't ask to sign in constantly.
- At the end: **delete the Service Auth policy and revoke the service token.**

### 2. MCP server: authenticate by Access JWT (`services/mcp_server/`)

- `/mcp` authenticates with the **`Cf-Access-Jwt-Assertion` header**, validated by the existing `validate_cf_jwt()` (signature, RS256, audience, expiry). Missing/invalid → 401.
- The `Authorization` header is **ignored** (with Managed OAuth it carries Cloudflare's opaque `oauth:…` token, which the origin can't and shouldn't validate). The server must **not** emit its own `WWW-Authenticate` challenges — Cloudflare owns the 401/discovery behavior.
- Identity: `user_id = email` from the JWT; `tenant_id = MCP_DEFAULT_TENANT_ID` (`family`). **Final state:** a valid JWT without `email` (e.g. a service token) → 403.
- **Transition only** (removed in the final step): while the old proxy still runs, a service-token JWT (no `email`, has `common_name`) is accepted as `user_id = MCP_DEFAULT_USER_ID`. *(Claude's call)* Set `MCP_DEFAULT_USER_ID` to Ivan's Access email for the transition so Desktop and phone write the same `added_by`. `/oauth/token` keeps working unchanged during the transition (the proxy still calls it; `/mcp` ignores the bearer it sends). The proxy is not modified: during the transition it may log a harmless error for the 202 on notifications.
- Startup: `CF_TEAM_DOMAIN` and `CF_MCP_AUDIENCE` required (already set in production).
- Existing data: one item with blank `added_by` — no backfill.

*Why the origin validates the JWT (standard practice):* Cloudflare Access is a reverse proxy; if a request ever reaches the origin around it (misconfigured route, a second hostname, a LAN/tunnel mistake), only origin-side validation stops it. Cloudflare's own docs say to enable Managed OAuth only for servers that validate the Access JWT.

### 3. MCP protocol compliance for remote clients

Bring `/mcp` up to the Streamable HTTP transport as remote clients expect:
- `initialize`: negotiate `protocolVersion` — echo the client's requested version if we support it, else answer with our latest supported (at least `2025-06-18`).
- JSON-RPC **notifications** (no `id`, e.g. `notifications/initialized`): respond **202 Accepted**, no body.
- `GET /mcp` (server→client SSE stream, which we don't offer): **405 Method Not Allowed**.
- `ping`: return `{}`.
- Responses stay `application/json` (allowed by the spec); no SSE needed.
- Don't **require** the `MCP-Protocol-Version` request header.

Exact required behavior to be confirmed against the current MCP spec and a live connect from claude.ai and ChatGPT.

### 4. Removal (final step)

Once the new connector works in Claude Desktop and Claude mobile:
- Repo: delete `tools/mcp_proxy/`; remove `/oauth/token`, `issue_token`/`validate_token`, the service-token transition branch, `MCP_JWT_SECRET` and `MCP_DEFAULT_USER_ID` (config, tests, `scripts/setup.sh`, `Makefile`, `CLAUDE.md`, `docs/main.spec.md`); drop `MCP_JWT_SECRET`/`MCP_DEFAULT_USER_ID` from the Lenovo `.env`.
- Mac: remove the proxy entry from Claude Desktop's config and the service-token entries from Keychain.
- Cloudflare: delete the Service Auth policy, revoke the service token.

## Rollout

1. Ship server changes (§2 incl. transition branch, §3) with tests; set `MCP_DEFAULT_USER_ID` to Ivan's Access email; deploy to the Lenovo; confirm Claude Desktop (old proxy) still works.
2. Cloudflare: add the email Allow policy, enable Managed OAuth, set redirect URIs. Confirm the old proxy still works.
3. On **claude.ai**: add the custom connector → sign in with email code. Disable the old proxy entry in Claude Desktop (keep it, don't delete yet) so tools don't appear twice.
4. Test "add eggs" in **Claude mobile** and in **Claude Desktop** through the new connector. **This is the switch-over gate.**
5. Removal (§4), deploy, re-test Claude Desktop + mobile.
6. ChatGPT (Go plan): add the custom connector on chatgpt.com, sign in, test on the phone. If Go only allows read-only connectors, record that and stop — no new tools for this.
7. Record results in the Obsidian security note (M4: per-person identity; service token removed).

**Rollback** before step 5: re-enable the old proxy entry in Claude Desktop; turn Managed OAuth off / remove the email policy if needed. After step 5 there is no fallback path by design (Ivan accepted: one path to maintain); fixing forward means fixing the OAuth path.

## Risks / unknowns (to verify, not to assume)

- **Service token during the transition**: Cloudflare doesn't say whether Service Auth still works with Managed OAuth on. If it breaks at step 2, the old proxy stops early — acceptable (go straight to steps 3–4); don't add a second hostname for it.
- **Protected Resource Metadata** (`/.well-known/oauth-protected-resource`, RFC 9728): Cloudflare says its discovery conforms to RFC 9728 and its 401 points clients to its metadata; whether the origin must serve anything is not stated. Verify with a live client; serve it from the origin only if needed.
- **Client compatibility**: Cloudflare doesn't name claude.ai/ChatGPT as tested clients; ChatGPT may prefer CIMD over DCR. Requires a live test; GitHub reports show claude.ai DCR failures with some setups. If claude.ai can't complete sign-in, stop before step 5 and investigate — the old path still works.
- **Access JWT claims**: confirm on the real token that user logins carry `email`.
- **Claude Desktop custom connectors**: confirm a connector added on claude.ai appears and works in Claude Desktop on the Mac.

## Testing

- Unit (`services/mcp_server/tests/`): valid user JWT → `user_id = email`; missing/invalid/expired/wrong-audience JWT → 401; valid JWT without `email` → transition: default user / final: 403; `Authorization` header alone (Belgrade JWT or `oauth:…`) → 401; no `WWW-Authenticate` header emitted; notifications → 202; `GET /mcp` → 405; `ping`; version negotiation. Locally generated RSA key, stubbed JWKS client.
- During the transition, existing `tools/mcp_proxy` tests keep passing; they're deleted with the proxy.
- Live: Claude Desktop (old proxy) after steps 1–2; claude.ai + Claude mobile + Claude Desktop (new connector) at step 4 and after step 5; ChatGPT at step 6.

## Follow-ups (not in this work)

- Per-person tool permissions if family members get access to more than the shared lists.

## Left to the builder

- Module layout of the auth change inside `mcp_server`, test fixtures, error message wording.
- Exact protocol versions supported, per the current MCP spec.
