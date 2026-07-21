# ,project-sandbox

[![ci](https://github.com/UDLRcodes/project-sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/UDLRcodes/project-sandbox/actions/workflows/ci.yml)
[![codeql](https://github.com/UDLRcodes/project-sandbox/actions/workflows/codeql.yml/badge.svg)](https://github.com/UDLRcodes/project-sandbox/actions/workflows/codeql.yml)

`,project-sandbox` spins up isolated, parallel copies of Docker Compose application stacks — a single project or a multi-project group — with per-instance networks, offset host ports, cloned baseline DB volumes, and managed git worktrees. Docker is the state store, so there is no daemon: instances are described by a small manifest and reconstructed deterministically from Docker labels plus a JSON registry.

## Requirements

- **docker** (Compose v2 — the `docker compose` subcommand)
- **git**
- **python3**
- **PyYAML** (for reading manifests and writing Compose overrides)

## Install

```bash
make install
```

This creates a private virtualenv at `~/.local/share/project-sandbox/venv` (with PyYAML),
then installs `,project-sandbox` to `~/.local/bin/,project-sandbox` with its shebang pointing
at that venv — so it is fully self-contained and touches nothing system-wide.

- Ensure `~/.local/bin` is on your `PATH`.
- `make uninstall` removes the script and its venv.

## Quick start

```bash
# 1. Draft a manifest by parsing your compose / .env files
,project-sandbox init webapp --project ~/Code/app

# 2. Review and edit the generated manifest (see "Manifest" below)

# 3. If the stack has a database, capture a baseline volume once
,project-sandbox baseline webapp

# 4. Bring up an isolated instance on its own network + offset ports
,project-sandbox up webapp --instance demo --branch feature/x

# 5. List running instances
,project-sandbox ls

# 6. Tear an instance down completely
,project-sandbox rm demo
```

## Manifest

Authored once per stack and stored (XDG-aware) at
`${XDG_CONFIG_HOME:-~/.config}/project-sandbox/stacks/<stack>.yml`. Example for a
unified multi-project web app group:

```yaml
stack: webapp
network: app_network              # one shared, per-instance network across all projects
worktree_parent: ~/Code/.worktrees   # absolute, ~ expanded

projects:
  - path: ~/Code/api
    creates_network: true        # brought up first
  - path: ~/Code/web
  - path: ~/Code/login
    rebuild_per_instance: true   # SPA bakes BACKEND_URL at build time

ports:                           # host ports to offset per instance (logical name -> default)
  API_WEB: 8080
  MAIL_UI: 8025
  MSSQL: 1433

env_rewrite:                     # host/browser-facing refs -> re-templated per instance
  ~/Code/api/.env:
    APP_URL: "http://localhost:${API_WEB}"
  ~/Code/login/.env:
    BACKEND_URL: "http://localhost:${API_WEB}"

db_baseline:
  - service: api-db
    volume: sqlserverdata        # cloned from baseline per instance
    strategy: clone
```

Key reference:

- **`stack`** — the stack name; matches the manifest filename and prefixes every instance's Compose project name.
- **`network`** — the shared logical network name. One per-instance copy (`<network>_<instance>`) is created and attached to every project, so inter-service DNS keeps working with zero port rewriting.
- **`worktree_parent`** — root directory under which git worktrees are created, as `<worktree_parent>/<stack>/<instance>/<project>`. `~` is expanded.
- **`projects[].path`** — path to a project containing a `docker-compose.yml`. `~` is expanded.
- **`projects[].creates_network`** — exactly one project sets this when `network` is present; it is brought up first and owns network creation.
- **`projects[].rebuild_per_instance`** — rebuild this project's image per instance (for stacks that bake host-facing config at build time), tagging a per-instance image.
- **`projects[].build_args`** — build args passed (and per-instance templated) when rebuilding.
- **`ports`** — logical port keys mapped to their default host ports. Each instance offsets these by a deterministic block to avoid collisions.
- **`env_rewrite`** — per `.env` file, host/browser-facing values re-templated to the instance's offset ports using `${PORT_KEY}` placeholders.
- **`db_baseline`** — database volumes cloned from a pristine baseline per instance so each instance starts seeded and diverges independently. Seed the baseline with **known test data/credentials** once (`,project-sandbox baseline <stack>`) so every instance is immediately usable (e.g. loginnable) with no per-instance data steps.
- **`projects[].copy_files`** — extra gitignored-but-required files to copy from the source repo into the worktree, at any relative path (e.g. `web/.env` in a subdir, `storage/oauth-private.key`, certs). `.env` at the repo root is copied automatically; list everything else here. Existing files in the worktree are never clobbered. Missing these is a common cause of runtime failures — e.g. Laravel Passport throwing `Invalid key supplied` because its OAuth keys weren't checked out.
- **`prewarm_volumes`** — `[{volume, service, path}]`. Named volumes populated once (from the service's image) before bring-up, so several services mounting the same *fresh* volume don't hit the concurrent image→volume population race (`mkdir ... file exists`). The `service` supplies the image; **`path` must be the volume's actual in-container mount path** — which may be a subdir (e.g. `/app/web/vendor`, not `/app/vendor`). A wrong `path` populates the wrong location and the race still fires.
- **`db_rewrite`** — `[{service, command, retries?, delay?}]`. After `up`, runs a templated command inside a DB service (via `docker compose exec -T`) to rewrite host-facing URLs stored *in the database* (e.g. an SSO routes table seeded from the baseline). `${PORT_KEY}` placeholders are substituted by the tool; shell vars like `$SA_PASSWORD` are left untouched for the *container* to expand (secrets stay out of the manifest). Retried until the DB is ready (`retries` default 10, `delay` default 3s).

> **Worktrees don't contain gitignored files.** A git worktree is a fresh checkout, so `.env` (and other gitignored files) aren't in it. The tool **auto-copies each project's source `.env`** into the worktree before `env_rewrite`; use `copy_files` for anything else a stack needs. Without this the stack would come up with almost no config (e.g. a blank DB password).

> **`env_rewrite` almost always needs hand-tuning per stack — treat the generated manifest as a
> starting point.** `init` only *suggests* candidates (commented) by scanning `.env` for
> `localhost:<port>` values; you must map each to the correct `${PORT_KEY}`. Not every
> `localhost` URL belongs to the stack (some point at *external* apps — leave those alone), and
> `.env` files drift over time so keys/values need re-checking. Note that values baked in at
> *build* time (e.g. an SPA compiling `BACKEND_URL` into its bundle) can't be fixed by
> `env_rewrite` — that project needs `rebuild_per_instance: true` + `build_args` instead.
> Verify with `--dry-run up` (it prints every planned rewrite) or by loading the app after `up`.

### Worked example: an SSO suite (`examples/webapp.yml`)

`examples/webapp.yml` is a real, validated three-project group (api + web +
login) whose full login flow was driven end-to-end in a browser. It's the best
reference for the harder cases, which recur in any non-trivial app:

- **Gitignored files at non-root paths** — `web`'s `.env` lives in a `web/`
  subdir; Passport's `storage/oauth-*.key` are gitignored. Both are handled with `copy_files`.
- **Server-side vs browser URLs** — the core distinction. A URL a container uses to call
  another container (e.g. web → api for token validation) must use the **internal
  service DNS** (`http://api-web/`), because inside a container `localhost:<hostport>` points
  at itself. A URL emitted to the **browser** (redirects, assets, AJAX) must use the instance's
  **offset host port**. The same `.env` often has both — classify each key.
- **Host-facing URLs can live in the database too** — this app stores SSO redirect targets in a
  `app_routes` table (seeded from the baseline with default ports). `env_rewrite` only covers
  `.env` files, so `db_rewrite` handles these: a templated `UPDATE ... REPLACE(...)` run inside
  `api-db` after `up`, rewriting the ports per instance (secret via the container's `$SA_PASSWORD`).
- **Apps outside the group** — a larger suite may reference other apps that aren't part of this
  sandbox; leave those URLs alone, or bring those apps up as their own stacks.

## Commands

| Command | Description |
|---|---|
| `init <stack>` | Generate a first-draft manifest by parsing compose / `.env` (user reviews). |
| `baseline <stack>` | Create / update the baseline DB volume(s). |
| `up <stack> --instance N [--branch B \| --path P]` | Worktrees + ports + clone + network + `compose up -d`. |
| `down <instance>` | `compose down` — keep volumes + worktree for fast re-`up`. |
| `rm <instance>` | Full teardown: `down -v`, drop clones, free ports, remove worktree (`--keep-worktree` to preserve). |
| `ls` | List all instances (Docker labels + registry): stack, instance, ports, worktree, health. |
| `status <instance>` | Detailed view of one instance. |
| `logs <instance> [service]` | Passthrough convenience. |
| `exec <instance> <service> -- …` | Passthrough convenience. |

## How isolation works

- Each instance runs under its own Compose project name `<stack>-<instance>`, so containers, default resources, and labels are namespaced per instance.
- A per-instance network `<network>_<instance>` is attached to every project as external; because service names are unchanged, container-to-container DNS keeps working with zero port rewriting.
- Host/browser-facing ports are offset by a deterministic per-instance block, and `env_rewrite` re-templates `.env` values (e.g. `APP_URL`, `BACKEND_URL`) to those offset ports.
- Each `db_baseline` volume is cloned per instance from a pristine baseline, so every instance starts seeded and diverges independently while the baseline stays untouched.

## Global flags

- `--dry-run` — print every action (and generated file) without executing anything.
- `--version` — print the version and exit.

## Trying it out

A tiered set of commands, from zero-risk to a full real run. Start at the top.

### 1. Zero-risk sanity checks

```bash
,project-sandbox --version
,project-sandbox --help
,project-sandbox ls          # queries Docker + registry; "no sandboxes" is expected at first
```

### 2. Prove parallel isolation with the built-in e2e test

Spins up **two** disposable nginx stacks on isolated ports/networks, checks both respond, and
tears them down. Needs Docker running.

```bash
cd path/to/project-sandbox
.venv/bin/pytest -m slow -v -s
```

### 3. Dry-run against a real stack (reads your repos, runs nothing)

Install the example manifest, then dry-run — it prints every docker/git command and writes the
generated override files for inspection, **without executing anything**:

```bash
mkdir -p ~/.config/project-sandbox/stacks
cp path/to/project-sandbox/examples/webapp.yml ~/.config/project-sandbox/stacks/

,project-sandbox --dry-run up webapp --instance test1 --branch feature/test

# inspect what it generated:
cat ~/.local/state/project-sandbox/instances/test1/*.override.yml
```

> Note: even in `--dry-run`, `up` records a registry entry and writes those override files (by
> design, so you can inspect them). Clear that bookkeeping with `,project-sandbox rm test1`.

### 4. Dry-run the manifest generator on any compose project

```bash
,project-sandbox --dry-run init scratch --project ~/Code/api
```

Prints a draft manifest to stdout (with `--dry-run` it does not write the file).

### 5. A real, self-contained run (no database needed)

Point a throwaway single-project stack at the bundled nginx fixture and actually bring it up:

```bash
printf 'stack: mini\nnetwork: mininet\nprojects:\n  - path: path/to/project-sandbox/tests/fixtures/mini-stack\n    creates_network: true\nports:\n  WEB: 18080\n' \
  > ~/.config/project-sandbox/stacks/mini.yml

,project-sandbox up mini --instance a --path path/to/project-sandbox/tests/fixtures/mini-stack
,project-sandbox up mini --instance b --path path/to/project-sandbox/tests/fixtures/mini-stack
,project-sandbox ls                       # two instances, different ports
open http://localhost:18180               # instance a
open http://localhost:18280               # instance b
,project-sandbox rm a && ,project-sandbox rm b
```

### 6. The full real run (when ready)

Needs the database baseline seeded first (see [`baseline`](#commands)):

```bash
,project-sandbox baseline webapp                                   # seed once
,project-sandbox up webapp --instance demo --branch feature/x   # real bring-up
,project-sandbox ls
,project-sandbox status demo
,project-sandbox logs demo api-web
,project-sandbox rm demo
```

## Development & CI

Set up a dev environment and run the same checks CI does:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make lint        # ruff check + ruff format --check
make typecheck   # mypy
make test        # full pytest suite (the 'slow' e2e test needs Docker)
make coverage    # non-slow suite with a 90% coverage gate
```

Every pull request runs, via GitHub Actions (all free for public repos, no secrets):

- **`ci.yml`** — ruff lint + format, mypy (advisory), pytest across Python 3.11–3.14 with a
  90% coverage gate, `pip-audit`, workflow linting (actionlint/yamllint), and the real-Docker
  e2e test. A `gate` job aggregates the required checks.
- **`codeql.yml`** — CodeQL security scanning (`security-extended`).
- **`mutation.yml`** — mutation testing (`mutmut`) as an adversarial "test-the-tests" layer;
  runs weekly, on demand, or when a PR is labelled `mutation` (advisory).

Property-based tests (Hypothesis) fuzz the pure-logic functions as part of the normal suite.

Optional: install the free-for-OSS [LlamaPReview](https://jetxu-llm.github.io/LlamaPReview-site/)
GitHub App for automated LLM code review on PRs (no secrets, no cost). Paid/secret-based LLM
review actions are intentionally not used.
