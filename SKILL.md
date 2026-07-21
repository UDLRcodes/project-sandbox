---
name: project-sandbox
description: >-
  Use when you need an isolated, runnable copy of an application stack — to implement or
  test a feature/ticket in its own environment, or to run multiple stack instances in
  parallel without port/network/volume collisions. Drives the `,project-sandbox` CLI to
  spin up (up), list (ls), inspect (status/logs/exec), and tear down (rm/down) sandboxes
  built from existing docker-compose stacks.
---

# project-sandbox

`,project-sandbox` spins up **isolated, parallel copies** of Docker Compose app stacks —
a single project or a multi-project group that boots together as one app. Each *instance*
gets its own Compose project, network, offset host ports, cloned baseline DB volumes, and
(optionally) git worktrees. Docker is the state store; there is no daemon.

**Primary use case:** an agent working a ticket spins up its own instance in its own git
worktree, runs/tests the full stack there, and tears it down when done — so many tickets
run in parallel with no collisions.

## The one rule that governs everything

The **instance name is the single uniqueness key**. It keys the Compose project, network,
volumes, ports, worktrees, and labels. So:

- Two copies of a stack = two different `--instance` names.
- Re-running `up` on an existing instance name is an **idempotent reconcile** (same ports),
  not a new copy.
- Pick a stable, unique instance name — a ticket id is ideal (e.g. `demo`, `prdemo`).

## Before you start

1. **Docker must be running** (Compose v2).
2. **A manifest must exist** for the stack at
   `${XDG_CONFIG_HOME:-~/.config}/project-sandbox/stacks/<stack>.yml`.
   List available stacks: `ls ~/.config/project-sandbox/stacks/`.
   If none exists, draft one: `,project-sandbox init <stack> --project ~/Code/<app>` then
   review it (set `worktree_parent`, `env_rewrite`, `db_baseline`).
3. **Always dry-run first** when unsure: prefix any command with `--dry-run` to print exactly
   what it will do (and write the generated compose overrides for inspection) without touching
   Docker.

## Core workflow (working a ticket)

```bash
# 0. (once per stack, if it has a database) seed the baseline volume
,project-sandbox baseline <stack>

# 1. preview, then bring up an isolated instance on its own branch/worktree
,project-sandbox --dry-run up <stack> --instance <ticket> --branch <branch>
,project-sandbox up <stack> --instance <ticket> --branch <branch>
#    -> prints the host URLs (offset ports) and the worktree path(s)

# 2. work inside the printed worktree path(s); the running stack reflects them
#    (bind mounts point at the worktree; the app is reachable on the offset ports)

# 3. inspect while working
,project-sandbox ls                      # all instances: stack, state, ports
,project-sandbox status <ticket>         # detail + `docker compose ps`
,project-sandbox logs <ticket> [service] # follow logs (Ctrl-C to stop)
,project-sandbox exec <ticket> <service> -- <cmd>   # run a command in a service

# 4. when finished, tear it down completely
,project-sandbox rm <ticket>             # removes containers, volumes, network, worktrees
```

## Command reference

| Command | What it does |
|---|---|
| `init <stack> --project <path> [--project …] [--force]` | Draft a manifest from compose/.env (review before use). |
| `baseline <stack> [--force]` | Create the baseline DB volume(s) declared in the manifest. Seed once; `up` clones per instance. |
| `up <stack> --instance <name> [--branch <b>] [--path <dir>]` | Create worktree(s)/ports/network, clone baseline volumes, generate overrides, `compose up -d`. Idempotent per instance. |
| `down <instance>` | `compose down` — keeps volumes + worktrees so a later `up` is fast. |
| `rm <instance> [--keep-worktree]` | Full teardown: down -v, drop cloned volumes + network, remove worktrees, drop registry entry. |
| `ls` | List every instance (Docker labels + registry): stack, state, ports. |
| `status <instance>` | Detail for one instance + container table. |
| `logs <instance> [service]` | `docker compose logs -f` passthrough. |
| `exec <instance> <service> -- <cmd>` | `docker compose exec` passthrough. |

Global flags: `--dry-run` (print/plan only), `--version`.

## Key behaviours to rely on

- **Container-to-container DNS keeps working with no port rewriting.** All projects in an
  instance share one per-instance network, so services resolve each other by their normal
  names (`api-db`, `api-web`, …). Only *host/browser-facing* URLs are offset (via the
  manifest's `ports` + `env_rewrite`).
- **Ports are deterministic and stable.** The same instance name always gets the same ports
  across re-runs — safe to reference in tests/bookmarks.
- **Baseline DB volumes are cloned, never mutated.** Each instance starts seeded and diverges
  independently; `rm` deletes the clone, never the baseline. Seed the baseline with **known test
  credentials** once so every instance is immediately loginnable — otherwise each fresh clone
  carries the baseline's password and needs a per-instance reset.
- **`--branch` creates the branch if it doesn't exist**, in a fresh worktree per project.
- **`.env` is auto-copied into worktrees.** A fresh worktree has no gitignored files, so the
  tool copies each project's source `.env` in before rewriting. Use `copy_files:` in the
  manifest for any *other* gitignored-but-required files (keys, certs). Without this the stack
  would boot with almost no config.
- **`prewarm_volumes` avoids fresh-volume races.** If several services share a brand-new named
  volume (e.g. `vendor`), declare it so the tool populates it once before bring-up (otherwise
  Docker's concurrent image→volume copy races: `mkdir ... file exists`). The entry's `path` must
  be the volume's *actual in-container mount path* — which may be a subdir (e.g.
  `/app/web/vendor`, not `/app/vendor`); a wrong path prewarms the wrong place and the
  race still fires.
- **A failed `up` is recoverable.** The instance is recorded before bring-up and compose
  failures produce a clean error (not a traceback) — so you can `status`/`logs` it, fix, and
  re-run `up`, or `rm` it.

## Environment variables & the `env_rewrite` section (read this)

There are **three classes** of references, handled very differently:

- **Compose service refs** (e.g. `DB_HOST=api-db`): resolved by *service DNS* on the shared
  per-instance network. **No changes** — service names are stable.
- **Server-side URLs used by one container to HTTP-call another** (e.g. web → api for SSO
  token validation, `api_internal`): rewrite to the **internal service DNS**
  (`http://api-web/`), NOT a host port — inside a container `localhost:<hostport>` points at
  *itself*, so a host-port URL used server-side silently fails.
- **Host / browser-facing URLs** (e.g. `APP_URL`, an SPA's `BACKEND_URL`, redirects, asset URLs,
  mailpit UI): rewrite to the instance's **offset host ports**.

The same `.env` often contains all three — classify each key. And note two things beyond
`.env`: (1) gitignored files needed at runtime (a `.env` in a subdir like `web/.env`,
Passport `storage/oauth-*.key`, certs) must be listed in `copy_files` or the app breaks at
runtime; (2) some apps store host-facing URLs **in the database** (e.g. an SSO `routes` table
seeded from the baseline), which `env_rewrite` does NOT touch — use **`db_rewrite`** (a
templated SQL command run inside a DB service after `up`, retried until the DB is ready) for
those. See `examples/webapp.yml` for a fully worked SSO example using `copy_files`,
`env_rewrite` (server/browser split), `prewarm_volumes`, and `db_rewrite`.

How it works: in the manifest, `env_rewrite` maps, per `.env` file, each env key to a template
using `${PORT_KEY}` placeholders (keys from the manifest's `ports:`). On `up`, the tool rewrites
those keys in the **worktree's** `.env` (never the original repo) to the instance's ports:

```yaml
ports:
  API_WEB: 8080
  WEB_PORT: 8081
env_rewrite:
  ~/Code/api/.env:
    APP_URL: "http://localhost:${API_WEB}"          # -> http://localhost:8180 for a +100 instance
    CLIENT_BASE_URL: "http://localhost:${WEB_PORT}"
```

**This almost always needs tweaking per stack — treat the generated manifest as a starting
point, not gospel:**

- `init` *suggests* candidates by scanning `.env` for `localhost:<port>` values, but it leaves
  them **commented** — you must review each and map it to the correct `${PORT_KEY}`.
- **Not every `localhost` URL belongs to this stack.** Some point at *external* apps (a separate
  service on another port). Leave those alone — rewriting them would mis-point the app.
- **`.env` files drift.** Re-check keys/values periodically; a key that moved or was renamed
  won't be rewritten and the browser will hit the wrong (or a stale) port.
- **Build-time values are different.** An SPA that bakes a URL at *build* time (e.g. Vite/webpack
  compiling `BACKEND_URL` into the bundle) can't be fixed by `env_rewrite` alone — that project
  needs `rebuild_per_instance: true` + `build_args` in the manifest so the value is compiled in
  per instance. `env_rewrite` only affects values read at *runtime*.

**Verify the remapping:**

- `,project-sandbox --dry-run up <stack> --instance <n>` prints every planned env rewrite
  (`+ rewrite env <path>: {...}`) so you can eyeball the mapping before running for real.
- After a real `up`, check the worktree's `.env`, or just load the app in a browser — if a link
  sends you to the wrong instance/port (or a `localhost` that isn't running), a rewrite is missing
  or wrong. Fix the manifest's `env_rewrite` and re-run `up` (idempotent).

## Gotchas / do-nots

- **Don't reuse an instance name across different stacks** — it errors (the name is the
  uniqueness key). Use distinct names.
- **Always `rm` (or `down`) when finished.** Running instances persist (Docker is the state
  store); orphaned instances hold ports and disk. `ls` shows what's live.
- **`--path` is single-project only** (use an existing worktree/dir). Multi-project groups
  must use `worktree_parent` in the manifest.
- **Edit code in the worktree that `up` created/used**, not the original repo — that's what the
  running stack mounts.
- `rm` is quiet and best-effort: it only removes resources that actually exist, so tearing down
  a phantom/half-created instance just prints `instance '<name>' removed` (no not-found noise),
  while genuine removal failures still surface.
- **Same-named volumes across group projects are auto-namespaced.** If two projects both define
  e.g. `vendor`, each gets its own volume (`<stack>-<instance>-<project>-<vol>`) — no collision.
  For `prewarm_volumes`, add one entry per project's copy of the volume (identified by a service
  in that project).

## Troubleshooting

- `host ports already in use: [...]` on a **new** instance → another process holds those host
  ports; pick a different instance offset by removing a stale instance (`ls` then `rm`) or free
  the ports.
- `manifest for stack '<x>' not found` → create it with `init` (see "Before you start").
- Nothing comes up / DNS fails → run the same `up` with `--dry-run` and inspect the generated
  overrides at `~/.local/state/project-sandbox/instances/<instance>/`.
