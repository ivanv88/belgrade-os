# Vault Service

Conflict-free file writer for the Obsidian vault. Serializes writes through a Redis stream so concurrent app tool calls never corrupt a note.

## Role

- Consumes `VaultOperation` protos from `tasks:vault_ops` (XREADGROUP)
- Acquires a per-path Redis lock before touching the filesystem
- Performs atomic writes (write to `.tmp`, then `os.rename`) to prevent partial reads
- Enforces path-containment: rejects any `path` that escapes `BEG_OS_VAULT_PATH`
- Supports `WRITE` and `DELETE` operations

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `tasks:vault_ops` (Redis stream, XREADGROUP) | Vault operations from apps via SDK `ctx.io` |
| Write/Delete | Filesystem at `BEG_OS_VAULT_PATH` | Obsidian vault on `/mnt/storage` |

## Key env vars

| Var | Default | Notes |
|---|---|---|
| `BEG_OS_VAULT_PATH` | `/tmp/belgrade-vault` | Root vault directory |
| `BEG_OS_REDIS_URL` | `redis://localhost:6379` | |
| `VAULT_REDIS_URL` | — | Overrides `BEG_OS_REDIS_URL` if set |
