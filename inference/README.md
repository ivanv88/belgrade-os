# Inference Controller

Runs the AI agent loop. Reads tasks from Redis, calls the LLM, drives the tool-use cycle, and streams results back.

## Role

- Consumes tasks from `tasks:inbound` (XREADGROUP)
- Fetches the registered tool list from Bridge at the start of each task
- Calls the configured LLM provider (Claude, Gemini, or Ollama) in a streaming loop
- For each tool call the LLM requests, routes to the correct stream:
  - `TRUSTED` tasks (no `app_id`) → `tasks:tool_calls` (bare-metal Runner)
  - `UNTRUSTED` tasks (with `app_id`) → `tasks:untrusted_calls` (EphemeralRunner in Platform Controller)
- Waits for tool results on `tasks:tool_results`, feeds them back to the LLM
- Publishes `ThoughtEvent` protos to `sse:{task_id}` after each LLM chunk, tool call, and on completion or error
- Checks `tasks:cancel:{task_id}` at the top of each tool loop iteration; cancels cleanly if set

## Trust model

`execution_mode` is stamped by the Gateway from `TRUSTED_USER_IDS`. As a defense-in-depth measure, the controller overrides `execution_mode` to `UNTRUSTED` for any task where `task.app_id != ""` — apps cannot self-assert trusted execution.

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `tasks:inbound` (Redis stream, XREADGROUP) | Incoming tasks |
| Write | `tasks:tool_calls` (Redis stream) | Trusted tool calls → Runner |
| Write | `tasks:untrusted_calls` (Redis stream) | Untrusted tool calls → Platform Controller |
| Read | `tasks:tool_results` (Redis stream, XREADGROUP) | Tool results back from runners |
| Write | `sse:{task_id}` (Redis pub/sub) | Streams ThoughtEvents to Gateway |
| Read | `tasks:cancel:{task_id}` (Redis key) | Cancel signal from `ctx.inference.cancel()` |
| HTTP out | Bridge `/v1/tools` | Fetch tool list at task start |

## Providers

Configured via env vars. Supports `anthropic` (Claude), `gemini`, and `ollama` (local LLMs via OpenAI-compatible API).
