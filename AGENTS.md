# AGENTS.md — `,project-sandbox`

Guidance for AI agents (and humans) working on this repo.

## What this is

A single-file Python CLI (`project_sandbox.py`, installed as `,project-sandbox`) that spins
up **isolated, parallel copies of Docker Compose app stacks** — a single project or a
multi-project group that boots together as one app. Each instance gets its own Compose
project, network, offset host ports, cloned baseline DB volumes, and (optionally) git
worktrees. Docker is the persistent state store; **there is no daemon**.

Primary use case: many AI agents each running a full, isolated stack copy for a ticket, in
parallel, without port/network/volume/container-name collisions.

## Layout

```
project_sandbox.py          # the entire tool (importable module; installed as ,project-sandbox)
tests/                      # pytest; one file per concern, mirrors the module's sections
tests/fixtures/mini-stack/  # nginx compose used by the real-Docker e2e test
examples/webapp.yml         # example multi-project manifest (api + web + login)
Makefile                    # test / lint / install / uninstall
```

## Dev setup & commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
make test                        # .venv/bin/pytest -q  (unit only; slow deselected via -m below)
.venv/bin/pytest -q -m "not slow"  # fast suite, no Docker needed
.venv/bin/pytest -m slow -v -s   # real-Docker e2e (needs a running daemon + free ports 18180/18280)
make lint                        # py_compile syntax check
make install                     # private venv + install to ~/.local/bin/,project-sandbox
```

`make install` is self-contained: it builds a venv at `~/.local/share/project-sandbox/venv`
(Homebrew python is PEP 668 externally-managed — do NOT `pip install --user`) and rewrites the
installed script's shebang to that venv. Runtime deps: `docker` (Compose v2), `git`, `python3`,
`PyYAML`.

## How to change code here (rules)

- **TDD is the workflow.** Write the failing test first, watch it fail, implement minimally,
  watch it pass, commit. Commits are small and Conventional (`feat:`, `test:`, `chore:`…).
- **Never shell out to Docker/git in unit tests.** All external commands go through the
  `Runner` class; unit tests use `Runner(dry_run=True)` and assert on `runner.calls`, or a
  `FakeRunner` subclass that returns canned `capture()` output. Only `@pytest.mark.slow`
  tests touch real Docker.
- **Every side-effecting command must honor `--dry-run`**: record/print the action, skip
  execution. Local artifacts (the JSON registry, generated override files) ARE written in
  dry-run; docker/git commands, `.env` writes, and `rmtree` are NOT.
- Keep it a single importable module with small pure functions — that's what makes it testable.
- Pure logic (paths, manifest, ports, env templating, override generation) has no I/O and is
  unit-tested directly; glue (`cmd_*`) is tested via `main()` + dry-run.

## Architecture at a glance

`main()` → argparse → name-based dispatch to a `cmd_<command>(args, runner)` function. The
pipeline pieces:

- **Paths** (`stacks_dir`, `registry_path`, `instance_dir`) — XDG-aware (`XDG_CONFIG_HOME`
  for manifests, `XDG_STATE_HOME` for registry + generated overrides).
- **Manifest** (`load_manifest`, `_validate_manifest`) — per-stack YAML; expands `~`.
- **Ports** (`allocate_offset`, `compute_ports`, `verify_ports_free`) — deterministic offset
  block per instance, persisted in the JSON registry, idempotent on re-`up`.
- **Env** (`render_template`, `rewrite_env_lines`) — `${PORT_KEY}` substitution into `.env`.
- **Override generation** (`derive_port_key_map`, `build_override`, `write_override`) — reads
  the original compose, produces a per-project Compose override.
- **Runner + docker/git helpers** (`Runner`, `compose_cmd`, `network_create`, `clone_volume`,
  `list_instances`, `worktree_*`).
- **Commands** (`cmd_up`/`down`/`rm`/`ls`/`status`/`init`/`baseline`/`logs`/`exec`).

## Invariants & gotchas (don't break these)

- **Instance name is the single uniqueness key.** Compose project (`<stack>-<instance>`),
  network (`<network>_<instance>`), cloned volumes (`<stack>-<instance>-<vol>`), labels, and
  worktree paths all derive from it. Two copies of a stack = two instance names.
- **Per-instance network preserves service DNS.** All projects join one per-instance network,
  so container-to-container references (`api-db`, `api-web`, …) need NO port rewriting. Only
  host/browser-facing URLs are offset (via `ports` + `env_rewrite`). `build_override` remaps
  *every* compose network key to the per-instance name regardless of the key (real stacks use
  `app_network` keyed to a `app_network` name — both are handled).
- **Worktrees exclude gitignored files.** A fresh worktree has no `.env` (it's gitignored). `cmd_up` auto-copies each project's source `.env` (plus manifest `copy_files`) into the worktree *before* `env_rewrite` — otherwise `env_rewrite` would write a near-empty `.env`, dropping `DB_PASSWORD` and all config. `_seed_worktree_files` fills gaps only (never clobbers).
- **Fresh shared volumes race on first population.** Several services mounting the same brand-new named volume trigger concurrent image→volume copies (`mkdir ... file exists`). `prewarm_volumes` populates such volumes once up front (`_prewarm_volume`, labeled so Compose adopts them). Normal dev never hits this because the volume persists.
- **Multi-project groups share ONE compose project**, so same-named named volumes across projects would collide (e.g. api + web both define `vendor`). Prevented by per-project namespacing: `_namespaced_volume_names` gives each project's non-baseline volumes an explicit unique name `<stack>-<instance>-<project>-<vol>` (via `build_override`'s `volume_names`). All instance volumes (namespaced + baseline clones) are recorded in the registry so `rm` removes them explicitly (Compose's `down -v` doesn't reliably drop explicit-named volumes).
- **`cmd_up` records the instance in the registry BEFORE bring-up** and wraps `docker compose up` to raise `SandboxError` (not a traceback) on failure, so a failed `up` is still `rm`-able. `rm` is best-effort AND quiet: it only removes resources that exist (`volume_exists`/`_network_exists`/`_project_has_containers`/`os.path.exists`), so a phantom instance tears down without not-found noise; in `--dry-run` it skips those checks and shows every command.
- **`db_rewrite` handles host-facing URLs stored in the DB** (e.g. an SSO routes table seeded from the baseline). `_run_db_rewrites` runs each entry's templated `command` inside a DB service via `compose exec -T` after the up loop, retrying until the DB is ready. Only `${PORT_KEY}` is substituted (via `render_template`); shell vars like `$SA_PASSWORD` pass through for the container to expand (secrets stay out of the manifest). Runs every `up` (idempotent REPLACE) because re-`up` re-clones the baseline volume.
- **`env_rewrite` is per-stack curation, not automatic.** `init` only *suggests* commented
  candidates (scanned for `localhost:<port>`); the manifest author maps each to the right
  `${PORT_KEY}`. Expect it to need tweaking: external-app URLs must be left alone, `.env` files
  drift, and build-time-baked values (e.g. an SPA's `BACKEND_URL`) need `rebuild_per_instance` +
  `build_args` rather than `env_rewrite` (which only affects runtime-read values). Don't assume a
  generated manifest is correct — verify with `--dry-run up`.
- **Compose merges `ports` by appending**, which would leave the original host port bound. We
  emit the `!override` YAML tag (`Override` class + `SandboxDumper`) so the ports list is
  *replaced*. Any new list field that must replace-not-merge needs the same treatment.
- **`container_name` is stripped/suffixed** in overrides — a hardcoded `container_name` is not
  project-prefixed by Docker and would collide across instances.
- **Analysis vs runtime compose:** `cmd_up` reads the *original* repo's compose for analysis
  (works in dry-run without a worktree), but passes the *worktree's* compose files to
  `docker compose -f` at runtime so `./`-relative bind mounts and build contexts resolve to the
  worktree. Keep that split.
- **`down` vs `rm`:** `down` = `docker compose -p <project> down` (keeps volumes + worktrees for
  fast re-`up`). `rm` = full teardown (`down -v`, remove cloned volumes + per-instance network,
  remove worktrees unless `--keep-worktree`, delete instance dir, drop registry entry).
- **Baseline volumes are cloned, never mutated.** `rm` deletes the per-instance clone, never the
  `<stack>-db-baseline` source.

## Adding a new subcommand

1. Add a subparser in `build_parser()`.
2. Add `cmd_<name>(args, runner)` (name-based dispatch finds it via `globals()`).
3. Route all execution through `runner`; support `--dry-run`.
4. Write `tests/test_cmd_<name>.py` with a dry-run `Runner` asserting the commands issued.

## Debugging

Prefer `,project-sandbox --dry-run <command> …` — it prints every docker/git action and writes
the generated override files to `~/.local/state/project-sandbox/instances/<instance>/` so you can
inspect exactly what would run without touching Docker.
