#!/usr/bin/env python3
""",project-sandbox — spin up isolated, parallel copies of Docker Compose app stacks.

Each instance gets its own Compose project, network, offset host ports, cloned baseline
DB volumes, and (optionally) git worktrees. Docker is the state store; there is no daemon.
"""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import os
import shutil
import sys

APP = "project-sandbox"


def _xdg(env_var: str, default_subpath: str) -> str:
    base = os.environ.get(env_var)
    if not base:
        base = os.path.join(os.environ["HOME"], default_subpath)
    return base


def config_home() -> str:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_home() -> str:
    return _xdg("XDG_STATE_HOME", ".local/state")


def stacks_dir() -> str:
    return os.path.join(config_home(), APP, "stacks")


def manifest_path(stack: str) -> str:
    return os.path.join(stacks_dir(), f"{stack}.yml")


def registry_path() -> str:
    return os.path.join(state_home(), APP, "registry.json")


def instance_dir(instance: str) -> str:
    return os.path.join(state_home(), APP, "instances", instance)


import yaml


class ManifestError(Exception):
    pass


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def load_manifest(stack: str) -> dict:
    path = manifest_path(stack)
    if not os.path.exists(path):
        raise ManifestError(f"manifest for stack '{stack}' not found at {path}")
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    _validate_manifest(data)
    # expand ~ in path fields
    if data.get("worktree_parent"):
        data["worktree_parent"] = _expand(data["worktree_parent"])
    for proj in data["projects"]:
        proj["path"] = _expand(proj["path"])
    if data.get("env_rewrite"):
        data["env_rewrite"] = {_expand(k): v for k, v in data["env_rewrite"].items()}
    return data


def _validate_manifest(data: dict) -> None:
    for key in ("stack", "projects", "ports"):
        if key not in data:
            raise ManifestError(f"manifest missing required key: '{key}'")
    if not isinstance(data["projects"], list) or not data["projects"]:
        raise ManifestError("'projects' must be a non-empty list")
    for proj in data["projects"]:
        if "path" not in proj:
            raise ManifestError("every project needs a 'path'")
    if data.get("network"):
        creators = [p for p in data["projects"] if p.get("creates_network")]
        if len(creators) != 1:
            raise ManifestError(
                "when 'network' is set, exactly one project must have creates_network: true"
            )
    for entry in data.get("db_rewrite", []) or []:
        if "service" not in entry or "command" not in entry:
            raise ManifestError("each db_rewrite entry needs 'service' and 'command'")
        if not isinstance(entry["command"], list):
            raise ManifestError("db_rewrite 'command' must be a list of args")


import json


def load_registry() -> dict:
    path = registry_path()
    if not os.path.exists(path):
        return {"instances": {}}
    with open(path) as fh:
        return json.load(fh)


def save_registry(reg: dict) -> None:
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(reg, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


BLOCK_SIZE = 100


def allocate_offset(reg: dict, instance: str) -> int:
    instances = reg.get("instances", {})
    if instance in instances and "offset" in instances[instance]:
        return instances[instance]["offset"]
    used = {i["offset"] for i in instances.values() if "offset" in i}
    candidate = BLOCK_SIZE
    while candidate in used:
        candidate += BLOCK_SIZE
    return candidate


def compute_ports(port_defaults: dict, offset: int) -> dict:
    return {key: default + offset for key, default in port_defaults.items()}


import socket


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _should_check_ports(reg: dict, instance: str, dry_run: bool) -> bool:
    # Only verify host ports for a brand-new instance. Re-running 'up' on a known
    # instance is an idempotent reconcile — its own ports are legitimately in use.
    if dry_run:
        return False
    return instance not in reg.get("instances", {})


def verify_ports_free(ports: dict) -> list[int]:
    return [p for p in ports.values() if not is_port_free(p)]


import re


class TemplateError(Exception):
    pass


def render_template(template: str, values: dict) -> str:
    def repl(match):
        key = match.group(1)
        if key not in values:
            raise TemplateError(f"unknown placeholder ${{{key}}} in template")
        return str(values[key])

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, template)


def rewrite_env_lines(lines: list[str], rewrites: dict) -> list[str]:
    remaining = dict(rewrites)
    out = []
    for line in lines:
        stripped = line.lstrip()
        matched = False
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}\n")
                matched = True
        if not matched:
            out.append(line)
    # ensure the last existing line ends with a newline before appending new keys,
    # else an appended key gets glued onto a file with no trailing newline
    if remaining and out and not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    for key, value in remaining.items():
        out.append(f"{key}={value}\n")
    return out


class Override(list):
    """A YAML sequence that emits the Docker Compose !override tag."""


class SandboxDumper(yaml.SafeDumper):
    pass


def _represent_override(dumper, data):
    return dumper.represent_sequence("!override", list(data))


def register_yaml_tags() -> None:
    SandboxDumper.add_representer(Override, _represent_override)


def _namespaced_volume_names(project_name, proj_basename, compose, exclude):
    # Give each project's named volumes a project-unique explicit name so two projects
    # in one group that declare the same volume key (e.g. both 'vendor') don't collide
    # into a single <project>_<key> volume. Excludes db_baseline volumes (handled as
    # external clones).
    return {
        vk: f"{project_name}-{proj_basename}-{vk}"
        for vk in (compose.get("volumes") or {})
        if vk not in exclude
    }


def build_override(
    compose,
    project,
    ports,
    instance,
    network_name,
    port_key_by_service,
    volume_renames=None,
    volume_names=None,
) -> dict:
    volume_renames = volume_renames or {}
    volume_names = volume_names or {}
    rebuild = bool(project.get("rebuild_per_instance"))
    rendered_args = (
        {k: render_template(v, ports) for k, v in project.get("build_args", {}).items()}
        if rebuild
        else {}
    )

    services_out = {}
    volumes_out = {}
    for svc_name, svc in compose.get("services", {}).items():
        entry: dict[str, object] = {}  # heterogeneous compose-service override fragment
        mapping = port_key_by_service.get(svc_name)
        if mapping:
            new_ports = []
            for container_port, port_key in mapping.items():
                new_ports.append(f"{ports[port_key]}:{container_port}")
            entry["ports"] = Override(new_ports)
        if svc.get("container_name"):
            entry["container_name"] = f"{svc['container_name']}_{instance}"

        new_volumes = []
        for vol in svc.get("volumes", []) or []:
            source = str(vol).split(":", 1)[0]
            if source in volume_renames:
                instance_vol = volume_renames[source]
                new_volumes.append(instance_vol + str(vol)[len(source) :])
                volumes_out[instance_vol] = {"name": instance_vol, "external": True}
            else:
                new_volumes.append(vol)
        if new_volumes:
            entry["volumes"] = new_volumes

        if rebuild and "build" in svc:
            entry["build"] = {"args": dict(rendered_args)}
            if svc.get("image"):
                entry["image"] = f"{svc['image']}-{instance}"

        if entry:
            services_out[svc_name] = entry

    # Per-project volume namespacing: set an explicit unique name on each named volume
    # (service mount keys stay the same; compose resolves them via the top-level name).
    for vol_key, new_name in volume_names.items():
        volumes_out.setdefault(vol_key, {})["name"] = new_name

    networks_out = {}
    if network_name:
        for net_name in compose.get("networks", {}):
            networks_out[net_name] = {"name": network_name, "external": True}

    override = {}
    if services_out:
        override["services"] = services_out
    if networks_out:
        override["networks"] = networks_out
    if volumes_out:
        override["volumes"] = volumes_out
    return override


def write_override(instance, project_name, override_dict) -> str:
    register_yaml_tags()
    directory = instance_dir(instance)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{project_name}.override.yml")
    with open(path, "w") as fh:
        yaml.dump(
            override_dict, fh, Dumper=SandboxDumper, default_flow_style=False, sort_keys=False
        )
    return path


def _to_int(value):
    """int(value) or None — never raises (a malformed compose port must not crash the tool)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_port_entry(entry):
    """Return (host_port:int|None, container_port:str) for a compose port entry.

    A non-numeric/malformed host yields None (the caller skips unmatched ports) rather
    than raising, so a bad entry in a compose file can't crash bring-up.
    """
    if isinstance(entry, dict):
        return _to_int(entry.get("published")), str(entry.get("target"))
    text = str(entry)
    parts = text.split(":")
    if len(parts) == 1:  # "80"
        return None, parts[0]
    if len(parts) == 2:  # "8080:80"
        return _to_int(parts[0]), parts[1]
    # "127.0.0.1:8080:80"
    return _to_int(parts[-2]), parts[-1]


def derive_port_key_map(compose, port_defaults):
    default_to_key = {v: k for k, v in port_defaults.items()}
    mapping = {}
    unmatched = []
    for svc_name, svc in compose.get("services", {}).items():
        for entry in svc.get("ports", []) or []:
            host, container = _parse_port_entry(entry)
            if host is None:
                continue
            if host in default_to_key:
                mapping.setdefault(svc_name, {})[container] = default_to_key[host]
            else:
                unmatched.append(host)
    return mapping, unmatched


import subprocess


class Runner:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.calls: list[list[str]] = []

    def run(self, cmd, check=True, **kw):
        self.calls.append(list(cmd))
        if self.dry_run:
            print("+ " + " ".join(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.run(cmd, check=check, **kw)

    def capture(self, cmd, check=True):
        self.calls.append(list(cmd))
        result = subprocess.run(cmd, check=check, capture_output=True, text=True)
        return result.stdout


def _resource_exists(runner, inspect_cmd) -> bool:
    result = runner.run(
        inspect_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return getattr(result, "returncode", 1) == 0


def compose_cmd(project, compose_files, args):
    cmd = ["docker", "compose", "-p", project]
    for f in compose_files:
        cmd += ["-f", f]
    return cmd + list(args)


def network_create(runner, name):
    # In dry-run always emit create (deterministic/testable).
    if runner.dry_run:
        runner.run(["docker", "network", "create", name])
        return
    if not _resource_exists(runner, ["docker", "network", "inspect", name]):
        runner.run(["docker", "network", "create", name])


def network_remove(runner, name):
    runner.run(["docker", "network", "rm", name], check=False)


def volume_exists(runner, name) -> bool:
    if runner.dry_run:
        return False
    return _resource_exists(runner, ["docker", "volume", "inspect", name])


def clone_volume(runner, src, dst):
    runner.run(["docker", "volume", "create", dst])
    runner.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{src}:/from",
            "-v",
            f"{dst}:/to",
            "alpine",
            "sh",
            "-c",
            "cp -a /from/. /to/",
        ]
    )


def remove_volume(runner, name):
    runner.run(["docker", "volume", "rm", name], check=False)


def list_instances(runner):
    # docker ps -a with our label, JSON per line; Labels is a comma-joined "k=v" string.
    out = runner.capture(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=project-sandbox.instance",
            "--format",
            "{{json .}}",
        ]
    )
    instances = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        labels = {}
        for pair in (obj.get("Labels", "") or "").split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels[k] = v
        inst = labels.get("project-sandbox.instance")
        if not inst:
            continue
        rec = instances.setdefault(
            inst,
            {
                "stack": labels.get("project-sandbox.stack"),
                "containers": [],
            },
        )
        rec["containers"].append(
            {
                "name": obj.get("Names"),
                "state": obj.get("State"),
                "status": obj.get("Status"),
            }
        )
    return instances


def worktree_dest(manifest, instance, project_path):
    parent = manifest.get("worktree_parent")
    if not parent:
        raise ManifestError("worktree_parent not set in manifest and no --path given")
    return os.path.join(
        parent, manifest["stack"], instance, os.path.basename(project_path.rstrip("/"))
    )


def worktree_add(runner, repo, dest, branch=None, create_branch=False):
    cmd = ["git", "-C", repo, "worktree", "add"]
    if create_branch and branch:
        cmd += ["-b", branch]
    cmd += [dest]
    if branch and not create_branch:
        cmd += [branch]
    runner.run(cmd)


def _seed_worktree_files(source_dir, dest_dir, extra_files=None):
    # Worktrees exclude gitignored files (e.g. .env). Copy the source repo's .env plus
    # any manifest-declared copy_files into a fresh worktree so the stack is configured.
    # Fills gaps only (never clobbers a file the worktree already has).
    for rel in [".env", *(extra_files or [])]:
        src = os.path.join(source_dir, rel)
        dst = os.path.join(dest_dir, rel)
        if os.path.abspath(src) == os.path.abspath(dst):
            continue
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copy2(src, dst)


def _run_db_rewrites(runner, manifest, project_name, ports, sleep_fn=None):
    # Rewrite host-facing URLs stored IN THE DATABASE (e.g. an SSO routes table seeded from the
    # baseline with default ports) to this instance's offset ports. Each entry runs a templated
    # command inside a DB service via `compose exec`, retrying until the DB is ready.
    # Note: only ${PORT_KEY} is substituted by the tool; shell vars like "$SA_PASSWORD" pass
    # through so the container (not the manifest) supplies secrets.
    import time as _time

    entries = manifest.get("db_rewrite", []) or []
    if not entries:
        return
    sleep_fn = sleep_fn or _time.sleep
    for entry in entries:
        service = entry["service"]
        cmd = [render_template(str(arg), ports) for arg in entry["command"]]
        full = compose_cmd(project_name, [], ["exec", "-T", service, *cmd])
        attempts = max(1, int(entry.get("retries", 10)))
        delay = float(entry.get("delay", 3))
        for i in range(attempts):
            result = runner.run(full, check=False)
            if runner.dry_run or getattr(result, "returncode", 1) == 0:
                break
            if i < attempts - 1:
                sleep_fn(delay)
        else:
            print(
                f"warning: db_rewrite for service '{service}' did not succeed after "
                f"{attempts} attempts (is the DB ready?)",
                file=sys.stderr,
            )


def _prewarm_volume(runner, instance_vol, vol_key, project_name, path, image):
    # Populate a fresh named volume once (single writer) to avoid the concurrent
    # image->volume population race when several services share it. instance_vol is
    # the volume's ACTUAL (namespaced) name; vol_key is its compose key (for labels).
    if not image:
        print(f"warning: prewarm skipped for volume '{vol_key}': no image found", file=sys.stderr)
        return
    if volume_exists(runner, instance_vol):
        return
    # Stamp Compose's labels so it adopts the pre-created volume silently (no
    # "not created by Docker Compose" warning) instead of treating it as foreign.
    runner.run(
        [
            "docker",
            "volume",
            "create",
            "--label",
            f"com.docker.compose.project={project_name}",
            "--label",
            f"com.docker.compose.volume={vol_key}",
            instance_vol,
        ]
    )
    runner.run(["docker", "run", "--rm", "-v", f"{instance_vol}:{path}", image, "true"])


def _remove_if_empty(path) -> bool:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            return True
    except OSError:
        pass
    return False


def worktree_remove(runner, repo, dest):
    # check=False: a missing/already-gone worktree must never abort a teardown.
    runner.run(["git", "-C", repo, "worktree", "remove", dest, "--force"], check=False)


class SandboxError(Exception):
    pass


_DESCRIPTION = (
    "Spin up isolated, parallel copies of Docker Compose app stacks — a single project\n"
    "or a multi-project group — each with its own network, offset host ports, cloned\n"
    "baseline DB volumes, and (optionally) git worktrees. Docker is the state store;\n"
    "there is no daemon."
)

_EXAMPLES = r"""
Examples:
  # Draft a manifest by parsing a project's compose / .env files
  ,project-sandbox init myapp --project ~/Code/myapp

  # Preview everything a bring-up would do, without touching Docker
  ,project-sandbox --dry-run up webapp --instance demo --branch feature/upload

  # Seed a baseline DB volume once, then clone it per instance on 'up'
  ,project-sandbox baseline webapp
  ,project-sandbox up webapp --instance demo --branch feature/upload

  # See every running sandbox, inspect one, follow its logs
  ,project-sandbox ls
  ,project-sandbox status demo
  ,project-sandbox logs demo api-web

  # Stop (keep volumes/worktrees) vs. fully remove
  ,project-sandbox down demo
  ,project-sandbox rm demo

Manifests live at ${XDG_CONFIG_HOME:-~/.config}/project-sandbox/stacks/<stack>.yml.
Run any command with --dry-run first to see exactly what it will do.
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog=",project-sandbox",
        description=_DESCRIPTION,
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="store_true", help="print the version and exit")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print every action (and generated file) without executing anything",
    )
    sub = p.add_subparsers(dest="command", metavar="<command>", title="commands")

    up = sub.add_parser(
        "up",
        help="create/refresh an isolated instance and bring its stack up",
        description="Create git worktree(s), allocate offset ports, clone baseline DB "
        "volume(s), create the per-instance network, generate compose "
        "overrides, and run 'docker compose up -d'. Idempotent per instance.",
    )
    up.add_argument("stack", help="stack name (matches a manifest in the stacks dir)")
    up.add_argument(
        "--instance",
        required=True,
        help="unique instance name; keys the network, ports, volumes, worktrees",
    )
    up.add_argument(
        "--branch", help="git branch to check out in the worktree(s) (created if missing)"
    )
    up.add_argument(
        "--path",
        help="use an existing worktree/dir instead of creating one (single-project stacks only)",
    )

    down = sub.add_parser(
        "down",
        help="stop an instance but keep its volumes and worktrees",
        description="Run 'docker compose down' for the instance. Volumes and worktrees "
        "are kept so a later 'up' is fast.",
    )
    down.add_argument("instance", help="instance name")

    rm = sub.add_parser(
        "rm",
        help="fully remove an instance (containers, volumes, network, worktrees)",
        description="Full teardown: 'down -v', remove cloned volumes and the per-instance "
        "network, remove worktrees, delete instance state, drop the registry entry.",
    )
    rm.add_argument("instance", help="instance name")
    rm.add_argument(
        "--keep-worktree", action="store_true", help="do not remove the git worktree(s)"
    )

    status = sub.add_parser(
        "status",
        help="show details for one instance",
        description="Print an instance's stack, ports, network, worktrees, and "
        "'docker compose ps' output.",
    )
    status.add_argument("instance", help="instance name")

    sub.add_parser(
        "ls",
        help="list all sandboxes (running + recorded)",
        description="List every instance from Docker labels joined with the "
        "registry: stack, state, and ports.",
    )

    bl = sub.add_parser(
        "baseline",
        help="create/update the baseline DB volume(s) for a stack",
        description="Create the baseline volume(s) declared in the manifest's db_baseline. "
        "Seed once; 'up' clones the baseline per instance.",
    )
    bl.add_argument("stack", help="stack name")
    bl.add_argument(
        "--force", action="store_true", help="recreate the baseline volume if it already exists"
    )

    ini = sub.add_parser(
        "init",
        help="generate a first-draft manifest from compose/.env",
        description="Parse the given project(s)' docker-compose.yml and .env to write a "
        "draft manifest for review. Refuses to overwrite without --force.",
    )
    ini.add_argument("stack", help="stack name (also the manifest filename)")
    ini.add_argument(
        "--project",
        action="append",
        default=[],
        metavar="PATH",
        help="a project directory to include (repeatable)",
    )
    ini.add_argument("--force", action="store_true", help="overwrite an existing manifest")

    lg = sub.add_parser(
        "logs",
        help="follow logs for an instance (compose logs -f)",
        description="Passthrough to 'docker compose logs -f' for the instance.",
    )
    lg.add_argument("instance", help="instance name")
    lg.add_argument("service", nargs="?", help="optional service to filter to")

    ex = sub.add_parser(
        "exec",
        help="run a command in an instance's service (compose exec)",
        description="Passthrough to 'docker compose exec <service> ...' for the instance.",
    )
    ex.add_argument("instance", help="instance name")
    ex.add_argument("service", help="service name")
    ex.add_argument(
        "cmd", nargs=argparse.REMAINDER, help="command to run (prefix with -- to separate flags)"
    )
    return p


def check_dependencies():
    missing = []
    if shutil.which("docker") is None:
        missing.append(("docker", "install Docker Desktop, or `brew install docker`"))
    if shutil.which("git") is None:
        missing.append(("git", "`brew install git`"))
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append(("PyYAML", "`pip install PyYAML`"))
    return missing


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    if getattr(args, "version", False):
        print(__version__)
        return 0
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    missing = check_dependencies()
    if missing:
        import sys as _sys

        for name, hint in missing:
            print(f"error: missing dependency '{name}': {hint}", file=_sys.stderr)
        return 1
    func = globals().get(f"cmd_{args.command}")
    if func is None:
        parser.error(f"unknown command: {args.command}")
    runner = Runner(dry_run=args.dry_run)
    try:
        return func(args, runner) or 0
    except (SandboxError, ManifestError, TemplateError) as e:
        print(f"error: {e}", file=__import__("sys").stderr)
        return 1


def _compose_files_for(project):
    base = project["path"]
    files = project.get("compose") or ["docker-compose.yml"]
    return [f if os.path.isabs(f) else os.path.join(base, f) for f in files]


def _load_compose(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _merge_compose(files):
    merged = {"services": {}, "networks": {}, "volumes": {}}
    for f in files:
        data = _load_compose(f)
        for section in ("services", "networks", "volumes"):
            merged[section].update(data.get(section) or {})
    return merged


def _branch_exists(runner, repo, branch):
    return _resource_exists(
        runner, ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
    )


def cmd_up(args, runner):
    manifest = load_manifest(args.stack)
    stack = manifest["stack"]
    instance = args.instance
    if args.path and len(manifest["projects"]) > 1:
        raise SandboxError(
            "--path is only valid for single-project stacks; groups use worktree_parent"
        )

    reg = load_registry()
    existing = reg["instances"].get(instance)
    if existing and existing.get("stack") not in (None, stack):
        raise SandboxError(f"instance '{instance}' already exists for stack '{existing['stack']}'")

    offset = allocate_offset(reg, instance)
    ports = compute_ports(manifest["ports"], offset)
    network_name = f"{manifest['network']}_{instance}" if manifest.get("network") else None
    project_name = f"{stack}-{instance}"

    # 1. Worktrees / working dirs
    working_dirs = {}
    worktree_paths = []
    for proj in manifest["projects"]:
        if args.path:
            working_dirs[proj["path"]] = args.path
        else:
            dest = worktree_dest(manifest, instance, proj["path"])
            create = False
            if args.branch and not runner.dry_run:
                create = not _branch_exists(runner, proj["path"], args.branch)
            if runner.dry_run or not os.path.exists(dest):
                worktree_add(runner, proj["path"], dest, branch=args.branch, create_branch=create)
            working_dirs[proj["path"]] = dest
            worktree_paths.append(dest)

    # 2. Live free-port check (new instances only; re-up is idempotent)
    if _should_check_ports(reg, instance, runner.dry_run):
        taken = verify_ports_free(ports)
        if taken:
            raise SandboxError(f"host ports already in use: {sorted(taken)}")

    # 3. DB baseline clones
    volume_renames = {}
    for entry in manifest.get("db_baseline", []) or []:
        vol = entry["volume"]
        baseline_name = entry.get("source") or f"{stack}-db-baseline"
        instance_vol = f"{stack}-{instance}-{vol}"
        clone_volume(runner, baseline_name, instance_vol)
        volume_renames[vol] = instance_vol

    # 4. Per-instance network
    if network_name:
        network_create(runner, network_name)

    # 5. Overrides + env rewrite (analysis reads ORIGINAL repo compose; runtime uses worktree)
    register_yaml_tags()
    per_project = []
    all_services = {}
    svc_vol_names = {}
    instance_volumes = set(volume_renames.values())  # tracked so 'rm' removes them all
    for proj in manifest["projects"]:
        wd = working_dirs[proj["path"]]
        analysis_files = _compose_files_for(proj)
        compose = _merge_compose(analysis_files)
        for svc_name, svc in compose.get("services", {}).items():
            all_services.setdefault(svc_name, svc)
        port_key_by_service, _unmatched = derive_port_key_map(compose, manifest["ports"])
        proj_name = os.path.basename(proj["path"].rstrip("/"))
        # per-project volume namespacing avoids collisions when two projects in the
        # group declare the same volume key (db_baseline volumes handled separately)
        proj_vol_names = _namespaced_volume_names(
            project_name, proj_name, compose, set(volume_renames)
        )
        instance_volumes.update(proj_vol_names.values())
        for svc_name in compose.get("services", {}):
            svc_vol_names[svc_name] = proj_vol_names
        override = build_override(
            compose=compose,
            project=proj,
            ports=ports,
            instance=instance,
            network_name=network_name,
            port_key_by_service=port_key_by_service,
            volume_renames=volume_renames,
            volume_names=proj_vol_names,
        )
        # stamp identifying labels on every service
        for svc_name in compose.get("services", {}):
            svc_entry = override.setdefault("services", {}).setdefault(svc_name, {})
            svc_entry["labels"] = [
                f"project-sandbox.instance={instance}",
                f"project-sandbox.stack={stack}",
            ]
        override_path = write_override(instance, proj_name, override)

        # seed gitignored-but-needed files (.env + manifest copy_files) into the worktree
        if not runner.dry_run and wd != proj["path"]:
            _seed_worktree_files(proj["path"], wd, proj.get("copy_files"))

        # env rewrite (relocate manifest env paths into the working dir)
        for env_key, mapping in (manifest.get("env_rewrite") or {}).items():
            if env_key.startswith(proj["path"]):
                rel = os.path.relpath(env_key, proj["path"])
                target_env = os.path.join(wd, rel)
                rewrites = {k: render_template(v, ports) for k, v in mapping.items()}
                if runner.dry_run:
                    print(f"+ rewrite env {target_env}: {rewrites}")
                else:
                    lines = (
                        open(target_env).read().splitlines(keepends=True)
                        if os.path.exists(target_env)
                        else []
                    )
                    with open(target_env, "w") as fh:
                        fh.writelines(rewrite_env_lines(lines, rewrites))

        runtime_files = _compose_files_for({**proj, "path": wd}) + [override_path]
        per_project.append((proj, runtime_files))

    # 5b. Pre-populate shared volumes once (single writer) to avoid the concurrent
    # image->volume population race when several services mount the same fresh volume.
    for entry in manifest.get("prewarm_volumes", []) or []:
        vol = entry["volume"]
        service = entry.get("service")
        image = entry.get("image") or (all_services.get(service) or {}).get("image")
        # resolve the volume's ACTUAL (namespaced) name via the owning service
        names = svc_vol_names.get(service, {})
        instance_vol = names.get(vol) or f"{project_name}_{vol}"
        _prewarm_volume(runner, instance_vol, vol, project_name, entry["path"], image)

    # 6. Record the instance BEFORE bring-up, so a failed 'up' is still tracked and
    # removable via 'rm' (rather than leaving untracked orphaned resources).
    reg["instances"][instance] = {
        "stack": stack,
        "offset": offset,
        "ports": ports,
        "worktrees": worktree_paths,
        "project_name": project_name,
        "network": network_name,
        "volumes": sorted(instance_volumes),
        "projects": [p["path"] for p in manifest["projects"]],
        "path_override": args.path,
    }
    save_registry(reg)

    # 7. Bring up in manifest order. Each project is a separate compose file under
    # one shared project name, so a per-project 'up' sees the OTHER projects'
    # containers as orphans — COMPOSE_IGNORE_ORPHANS silences that false alarm.
    up_env = {**os.environ, "COMPOSE_IGNORE_ORPHANS": "1"}
    try:
        for proj, runtime_files in per_project:
            up_args = ["up", "-d"]
            if proj.get("rebuild_per_instance"):
                up_args.append("--build")
            runner.run(compose_cmd(project_name, runtime_files, up_args), env=up_env)
    except subprocess.CalledProcessError as e:
        raise SandboxError(
            f"bring-up failed: docker compose exited {e.returncode}. "
            f"Inspect with ',project-sandbox status {instance}' or "
            f"',project-sandbox logs {instance} <service>'. Fix the cause and re-run "
            f"'up {instance}' (idempotent), or ',project-sandbox rm {instance}' to tear it down."
        ) from e

    # 7b. Post-up DB rewrites (host-facing URLs stored in the DB, templated to this instance).
    _run_db_rewrites(runner, manifest, project_name, ports)

    # 8. Summary
    print(f"instance '{instance}' up  (compose project: {project_name})")
    for key, port in sorted(ports.items()):
        print(f"  {key}: localhost:{port}")
    for wt in worktree_paths:
        print(f"  worktree: {wt}")
    return 0


def _reg_entry(instance):
    reg = load_registry()
    entry = reg["instances"].get(instance)
    if not entry:
        raise SandboxError(f"unknown instance: {instance}")
    return reg, entry


def _network_exists(runner, name):
    return _resource_exists(runner, ["docker", "network", "inspect", name])


def _project_has_containers(runner, project_name):
    out = runner.capture(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project_name}"],
        check=False,
    )
    return bool(out.strip())


def cmd_down(args, runner):
    _reg, entry = _reg_entry(args.instance)
    runner.run(compose_cmd(entry["project_name"], [], ["down"]))
    print(f"instance '{args.instance}' stopped (volumes + worktrees kept)")
    return 0


def cmd_rm(args, runner):
    reg, entry = _reg_entry(args.instance)
    # Teardown is best-effort AND quiet: only touch resources that actually exist, so a
    # phantom/half-created instance tears down cleanly without spewing not-found errors.
    # In --dry-run we skip the existence checks and show every command that would run.
    dry = runner.dry_run
    if dry or _project_has_containers(runner, entry["project_name"]):
        runner.run(compose_cmd(entry["project_name"], [], ["down", "-v"]), check=False)
    for vol in entry.get("volumes", []) or []:
        if dry or volume_exists(runner, vol):
            remove_volume(runner, vol)
    if entry.get("network") and (dry or _network_exists(runner, entry["network"])):
        network_remove(runner, entry["network"])
    if not getattr(args, "keep_worktree", False):
        worktrees = entry.get("worktrees", []) or []
        for repo, wt in zip(entry.get("projects", []) or [], worktrees):
            if dry or os.path.exists(wt):
                worktree_remove(runner, repo, wt)
        # drop the now-empty per-instance worktree dir (<parent>/<stack>/<instance>)
        if not dry:
            for parent in {os.path.dirname(wt) for wt in worktrees}:
                _remove_if_empty(parent)
    idir = instance_dir(args.instance)
    if os.path.isdir(idir) and not runner.dry_run:
        shutil.rmtree(idir)
    del reg["instances"][args.instance]
    save_registry(reg)
    print(f"instance '{args.instance}' removed")
    return 0


def cmd_ls(args, runner):
    reg = load_registry()
    try:
        live = list_instances(runner)
    except Exception:
        live = {}
    names = sorted(set(reg["instances"]) | set(live))
    if not names:
        print("no sandboxes")
        return 0
    print(f"{'INSTANCE':<16} {'STACK':<12} {'STATE':<10} PORTS")
    for name in names:
        entry = reg["instances"].get(name, {})
        stack = entry.get("stack") or live.get(name, {}).get("stack") or "?"
        containers = live.get(name, {}).get("containers", [])
        running = sum(1 for c in containers if c.get("state") == "running")
        total = len(containers)
        state = f"{running}/{total} up" if total else "stopped"
        ports = ",".join(f"{k}:{v}" for k, v in sorted((entry.get("ports") or {}).items()))
        print(f"{name:<16} {stack:<12} {state:<10} {ports}")
    return 0


def cmd_status(args, runner):
    _reg, entry = _reg_entry(args.instance)
    print(f"instance: {args.instance}")
    print(f"stack:    {entry.get('stack')}")
    print(f"project:  {entry.get('project_name')}")
    print(f"network:  {entry.get('network')}")
    print("ports:")
    for k, v in sorted((entry.get("ports") or {}).items()):
        print(f"  {k}: localhost:{v}")
    print("worktrees:")
    for wt in entry.get("worktrees", []) or []:
        print(f"  {wt}")
    print("containers:")
    sys.stdout.flush()  # keep our output ordered before docker's direct writes
    runner.run(compose_cmd(entry["project_name"], [], ["ps"]), check=False)
    return 0


def _logical_port_key(service, container_port):
    base = re.sub(r"[^A-Za-z0-9]+", "_", service).upper().strip("_")
    return f"{base}_{container_port}"


def cmd_init(args, runner):
    stack = args.stack
    if not args.project:
        raise SandboxError("init requires at least one --project <path>")
    out_path = manifest_path(stack)
    if os.path.exists(out_path) and not args.force:
        raise SandboxError(f"manifest already exists at {out_path} (use --force to overwrite)")

    proj_paths = [os.path.expanduser(p) for p in args.project]
    ports = {}
    networks = []
    env_suggestions = []
    for ppath in proj_paths:
        cfile = os.path.join(ppath, "docker-compose.yml")
        compose = (
            _merge_compose([cfile]) if os.path.exists(cfile) else {"services": {}, "networks": {}}
        )
        for svc_name, svc in compose.get("services", {}).items():
            for entry in svc.get("ports", []) or []:
                host, container = _parse_port_entry(entry)
                if host is None:
                    continue
                ports[_logical_port_key(svc_name, container)] = host
        for net in compose.get("networks", {}):
            if net not in networks:
                networks.append(net)
        env_file = os.path.join(ppath, ".env")
        scan_file = env_file if os.path.exists(env_file) else os.path.join(ppath, ".env.example")
        if os.path.exists(scan_file):
            for ln in open(scan_file):
                m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*localhost:\d+.*)", ln)
                if m:
                    env_suggestions.append((env_file, m.group(1), m.group(2).strip()))

    net_name = networks[0] if networks else None
    lines = [
        f"# project-sandbox manifest for stack '{stack}' (GENERATED DRAFT - review me)",
        f"stack: {stack}",
    ]
    if net_name:
        lines.append(f"network: {net_name}")
    lines.append("worktree_parent: ~/Code/.worktrees   # TODO set desired parent dir")
    lines.append("")
    lines.append("projects:")
    for i, ppath in enumerate(proj_paths):
        lines.append(f"  - path: {ppath}")
        if i == 0 and net_name:
            lines.append("    creates_network: true")
    lines.append("")
    lines.append("ports:")
    for k, v in ports.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("# env_rewrite: rewrite host/browser-facing URLs to the instance's offset ports.")
    lines.append(
        '# Map each key below to the right port template, e.g. "http://localhost:${API_WEB_80}".'
    )
    for env_path, key, val in env_suggestions:
        lines.append(f"#   {env_path} -> {key}: {val}")
    lines.append("")
    lines.append("# db_baseline:  # uncomment for stacks with a database volume to clone")
    lines.append("#   - service: <db-service>")
    lines.append("#     volume: <named-volume>")
    text = "\n".join(lines) + "\n"

    if runner.dry_run:
        print(text)
        return 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(text)
    print(f"wrote draft manifest: {out_path}")
    print("Review it: set worktree_parent, env_rewrite, db_baseline; verify ports.")
    return 0


def cmd_baseline(args, runner):
    manifest = load_manifest(args.stack)
    stack = manifest["stack"]
    entries = manifest.get("db_baseline", []) or []
    if not entries:
        print(f"stack '{stack}' declares no db_baseline; nothing to do")
        return 0
    for entry in entries:
        baseline_name = entry.get("source") or f"{stack}-db-baseline"
        if volume_exists(runner, baseline_name) and not args.force:
            raise SandboxError(
                f"baseline volume '{baseline_name}' already exists (use --force to recreate)"
            )
        if args.force:
            remove_volume(runner, baseline_name)
        runner.run(["docker", "volume", "create", baseline_name])
        print(f"created baseline volume '{baseline_name}' for service '{entry['service']}'.")
    print(
        "Seed the baseline volume(s): start your DB against the baseline volume, "
        "restore/migrate/seed, then stop. 'up' clones the baseline per instance."
    )
    return 0


def cmd_logs(args, runner):
    _reg, entry = _reg_entry(args.instance)
    a = ["logs", "-f"]
    if args.service:
        a.append(args.service)
    runner.run(compose_cmd(entry["project_name"], [], a))
    return 0


def cmd_exec(args, runner):
    _reg, entry = _reg_entry(args.instance)
    cmd = list(args.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    runner.run(compose_cmd(entry["project_name"], [], ["exec", args.service, *cmd]))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
