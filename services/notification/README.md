# Notification Service

Delivers push notifications. Reads notification requests from a Redis stream and dispatches them through the configured driver.

## Role

- Consumes `NotificationRequest` protos from `tasks:notifications` (XREADGROUP)
- Routes each request through the active driver (`ntfy` by default; Firebase planned)
- On driver failure, moves the message to a dead-letter stream (`tasks:notifications:failed`) for later inspection
- Reconnects automatically on Redis connection loss

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `tasks:notifications` (Redis stream, XREADGROUP) | Incoming notification requests |
| Write | `tasks:notifications:failed` (Redis stream) | Dead-letter queue for failed deliveries |

## Drivers

| Driver | Notes |
|---|---|
| `ntfy` | Default; POSTs to configured ntfy topic |
| Firebase | Planned for Phase 2 |

Configured via `BEG_OS_NOTIFICATION_DRIVER` env var, overridable per-app in `manifest.json`.
