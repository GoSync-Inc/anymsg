#!/usr/bin/env python3
"""Verified, explicit production promotion. No provider credentials or migrations."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import urllib.parse
import urllib.request

REPOSITORY = "GoSync-Inc/deedkit"
REQUIRED_JOBS = {"checks / verify", "native / synthetic", "images / build (backup)", "CI gate"}
# A main push run may skip only this job, and only when the merge queue verified the exact commit (ANYM-321).
QUEUE_VERIFIED_JOB = "checks / verify"
QUEUE_BRANCH_PREFIX = "gh-readonly-queue/main/"
MAX_PAGES = 10
MIN_FREE_BYTES = 15 * 1024**3
BUILDS = {
    "runtime": (".", "services/messaging/Dockerfile", "runtime", "import messaging_service, telegram_broker, telegram_session_owner, platform_contracts.release"),
    "indexer": (".", "services/messaging/Dockerfile", "indexer", "import messaging_service.file_index"),
    "enricher-local": (".", "services/messaging/Dockerfile", "enricher-local", "import messaging_service.enrichment"),
    "whatsapp-native": (".", "services/messaging/Dockerfile", "whatsapp-native", "import messaging_service.whatsapp_collector"),
    "gateway": (".", "services/gateway/Dockerfile", "", "import agent_gateway, platform_contracts.release"),
    "calendar": ("services/calendar", "services/calendar/Dockerfile", "", "import family_calendar"),
    "calls": ("services/calls", "services/calls/Dockerfile", "", "import fastapi, pipecat"),
    "backup": (".", "services/messaging/Dockerfile", "backup",
               "import messaging_service.backup_restore_worker; import subprocess; "
               "[subprocess.run([tool, '--version'], check=True) for tool in ('age', 'pg_dump', 'pg_restore')]"),
    "http-router": (".", "services/release-router/Dockerfile", "", ""),
}


class Refused(RuntimeError):
    pass


def run(argv, *, cwd=None, umask=-1):
    # Never include command output in an exception: Compose can contain secrets.
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=1800, umask=umask)
    if result.returncode:
        raise Refused(f"{argv[0]} failed (exit {result.returncode}); production was not accepted")
    return result.stdout


def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "deedkit-production"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + "/" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise Refused("GitHub response exceeded the safety limit")
    return json.loads(payload)


def complete_listing(fetch, key):
    """Every item of a paginated GitHub listing, or Refused when the pages cannot prove they are complete.

    A page that reports `total_count` must agree with every other page, and the distinct items collected
    must reach exactly that count. A page without a count is complete only when it is shorter than a page.
    """
    items, total = {}, None
    for page in range(1, MAX_PAGES + 1):
        listing = fetch(page)
        chunk = listing[key]
        count = listing.get("total_count")
        if count is not None:
            if not isinstance(count, int) or isinstance(count, bool) or count < 0 or total not in (None, count):
                raise Refused("the CI listing reported an invalid or changing total")
            total = count
        for item in chunk:
            items[item.get("id", len(items))] = item
        if total is not None:
            if len(items) > total:
                raise Refused("the CI listing returned more items than its total")
            if len(items) == total:
                return list(items.values())
            if not chunk:
                break
        elif len(chunk) < 100:
            return list(items.values())
    raise Refused("the CI listing could not be read completely")


def latest_run(sha, event, get):
    """The newest ci.yml run of `event` for `sha`, from a complete listing, or None."""
    query = {"event": event, "head_sha": sha, "per_page": 100}
    if event == "push":
        query["branch"] = "main"
    runs = complete_listing(lambda page: get("actions/workflows/ci.yml/runs?" + urllib.parse.urlencode(
        {**query, "page": page})), "workflow_runs")
    # A later failed/pending rerun must supersede a previous successful run.
    return max(runs, key=lambda value: value["id"]) if runs else None


def attempt_jobs(run, get):
    return complete_listing(lambda page: get(
        f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100&page={page}"), "jobs")


def job_concluded(jobs, name, conclusion="success"):
    matches = [job for job in jobs if job["name"] == name]
    return len(matches) == 1 and matches[0].get("status") == "completed" and matches[0].get("conclusion") == conclusion


def unchanged(run, again):
    """Whether a re-read shows the same run, attempt and result, so no rerun started between the reads."""
    return again is not None and all(again.get(key) == run.get(key)
                                     for key in ("id", "run_attempt", "status", "conclusion"))


def queue_verification(sha, get):
    """The merge queue run whose `checks / verify` and `CI gate` verified exactly this commit."""
    queue = latest_run(sha, "merge_group", get)
    if queue is None:
        raise Refused("the main push run skipped checks / verify and no merge queue run verified this commit")
    if (queue.get("head_sha") != sha or queue.get("event") != "merge_group"
            or not str(queue.get("head_branch", "")).startswith(QUEUE_BRANCH_PREFIX)
            or queue.get("status") != "completed" or queue.get("conclusion") != "success"
            or queue.get("head_repository", {}).get("full_name") != REPOSITORY):
        raise Refused("latest merge queue run is not a completed success of this repository's main queue for this commit")
    jobs = attempt_jobs(queue, get)
    for required in (QUEUE_VERIFIED_JOB, "CI gate"):
        if not job_concluded(jobs, required):
            raise Refused(f"merge queue job did not pass: {required}")
    if not unchanged(queue, latest_run(sha, "merge_group", get)):
        raise Refused("the merge queue run changed while it was being verified")
    return queue


def verify(sha, get=api):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused("an exact lowercase 40-character commit SHA is required")
    if get("git/ref/heads/main")["object"]["sha"] != sha:
        raise Refused("candidate is not the current main commit")
    latest = latest_run(sha, "push", get)
    if latest is None:
        raise Refused("main has no CI push run for this exact commit")
    if (latest.get("head_sha") != sha or latest.get("head_branch") != "main"
            or latest.get("event") != "push" or latest.get("status") != "completed"
            or latest.get("conclusion") != "success"
            or latest.get("head_repository", {}).get("full_name") != REPOSITORY):
        raise Refused("latest main CI run is not a completed success for this repository and commit")
    jobs = attempt_jobs(latest, get)
    verified_by = latest
    for required in sorted(REQUIRED_JOBS):
        if required == QUEUE_VERIFIED_JOB and job_concluded(jobs, required, "skipped"):
            # The push run reused the queue's verification; GitHub, not the push run, must show it passed.
            verified_by = queue_verification(sha, get)
        elif not job_concluded(jobs, required):
            raise Refused(f"required job did not pass: {required}")
    if not unchanged(latest, latest_run(sha, "push", get)):
        raise Refused("the main CI push run changed while it was being verified")
    return {"sha": sha, "run_id": latest["id"], "run_attempt": latest["run_attempt"], "url": latest["html_url"],
            "verify_run_id": verified_by["id"], "verify_run_attempt": verified_by["run_attempt"],
            "verify_run_url": verified_by["html_url"]}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def config(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
        raise Refused("production config must be owned by the operator and not writable by others")
    value = read(path)
    if value.get("version") != 1 or not value.get("projects"):
        raise Refused("invalid production configuration")
    state = Path(value["state_directory"])
    if not state.is_absolute() or state.is_symlink():
        raise Refused("state directory must be an absolute non-symlink path")
    seen = set()
    for project in value["projects"]:
        name = project["name"]
        if name in seen or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise Refused("invalid or duplicate Compose project")
        seen.add(name)
        if not project.get("services") or set(project["services"].values()) - BUILDS.keys():
            raise Refused("select explicit services with known build targets")
        if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", service)
               or service in {"db", "calendar-db", "database", "postgres", "operator"}
               for service in project["services"]):
            raise Refused("only named application services may be promoted")
        for filename in [project["directory"], *project["files"], *project.get("env_files", [])]:
            if not Path(filename).is_absolute() or not Path(filename).exists():
                raise Refused("Compose paths must be absolute and already exist")
    return value


@contextmanager
def lock(state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_uid != os.geteuid() or state.stat().st_mode & 0o077:
        raise Refused("production state directory must be private and operator-owned")
    with (state / "deploy.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another production operation holds the deployment lock") from exc
        yield


def compose(project):
    # Config omits inactive profiles by default, even for currently running
    # services. Loading profiles does not start them; apply selects exact names.
    result = ["docker", "compose", "--profile", "*", "--project-name", project["name"], "--project-directory", project["directory"]]
    for filename in project["files"]:
        result += ["-f", filename]
    for filename in project.get("env_files", []):
        result += ["--env-file", filename]
    return result


def snapshot(project):
    document = json.loads(run(compose(project) + ["config", "--format", "json"]))
    running = {}
    for service in project["services"]:
        ids = run(["docker", "ps", "--filter", "label=com.docker.compose.project=" + project["name"],
                   "--filter", "label=com.docker.compose.service=" + service, "--format", "{{.ID}}"]).split()
        if len(ids) != 1 or service not in document["services"]:
            raise Refused("each selected service must already have exactly one running container")
        validate_runtime(document["services"][service])
        actual = json.loads(run(["docker", "inspect", ids[0]]))[0]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", actual["Image"]):
            raise Refused("running container has no immutable image identity")
        running[service] = {"container": ids[0], "image": actual["Image"]}
    return document, running


# The runtime's data/config mount roots, never executable roots.
MOUNT_ROOTS = ("/data", "/sessions", "/spool", "/run/secrets", "/run/deedkit",
               "/models", "/processing-locks", "/broker", "/voice-broker")
# A mount root named inside a larger argument, e.g. inline code or `--require=/data/x.js`.
MOUNT_REFERENCE = re.compile(r"(?<![\w./-])(?:" + "|".join(map(re.escape, MOUNT_ROOTS)) + r")(?![\w.-])")
CODE_SUFFIXES = (".py", ".pyc", ".pth", ".so", ".js", ".mjs", ".cjs", ".sh")
# Options whose value is a configuration/data file: the only mounted paths a command may name.
DATA_OPTIONS = {"--config", "--policy", "--home", "--dsn-file"}
SHELLS = {"sh", "bash", "dash", "ash", "zsh", "ksh", "busybox"}
NODE_CODE_OPTIONS = {"-e", "--eval", "-p", "--print", "-r", "--require", "--import",
                     "--loader", "--experimental-loader", "-i", "--interactive"}
# Diagnostics/encoding only. Every other PYTHON* setting can change imports or code
# (PYTHONPATH, PYTHONHOME, PYTHONUSERBASE, PYTHONSTARTUP, PYTHONSAFEPATH, ...).
PYTHON_VARIABLES = {"PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONFAULTHANDLER",
                    "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONNOUSERSITE", "PYTHONHASHSEED", "PYTHONWARNINGS"}
LOADER_VARIABLES = {"NODE_OPTIONS", "NODE_PATH", "NODE_REPL_EXTERNAL_MODULE", "GCONV_PATH",
                    "BASH_ENV", "ENV", "PERL5LIB", "PERL5OPT", "RUBYLIB", "RUBYOPT"}
HOST_NAMESPACES = ("network_mode", "pid", "ipc", "uts", "userns_mode", "cgroup")
UNCONFINED = re.compile(r"(seccomp|apparmor|systempaths)[=:]unconfined|label[=:]disable")
CODE_REFUSED = "host-mounted code or an unknown mount root bypasses verified images; package extensions in main first"
IMPORT_REFUSED = "runtime import path overrides bypass verified images"
INLINE_REFUSED = "inline shell or interpreter code bypasses verified images"
PRIVILEGE_REFUSED = "privileged settings or host namespaces bypass the reviewed container sandbox"


def mounted(value):
    # Relative paths are also resolved from "/": an image WORKDIR may be the root.
    path = posixpath.normpath(posixpath.join("/", str(value)))
    return any(path == root or path.startswith(root + "/") for root in MOUNT_ROOTS)


def unsafe_variable(name, value):
    if name.startswith("PYTHON"):
        return name not in PYTHON_VARIABLES
    if name.startswith("LD_") or name in LOADER_VARIABLES:
        return True
    if name == "PATH":
        return any(not entry.startswith("/") or mounted(entry) for entry in str(value or "").split(":"))
    # A user site directory under HOME is imported and runs its .pth files.
    return name == "HOME" and mounted(value or "/")


def python_code(arguments):
    values = iter(arguments)
    for item in values:
        if item == "-":
            return "inline"
        if item.startswith("--"):
            if item == "--check-hash-based-pycs":
                next(values, None)
            continue
        if not item.startswith("-"):
            return None  # A script: validate_argv checks it as a path.
        for index, flag in enumerate(item[1:], 1):
            if flag == "c":
                return "inline"
            if flag in "mWX":
                value = item[index + 1:] or next(values, "")
                # With WORKDIR /, `-m broker.x` imports a host-mounted directory.
                if flag == "m":
                    return "module" if mounted(value.partition(".")[0] or "/") else None
                break
    return None


def interpreter_code(argv):
    # Every position may start a program: wrappers such as nice or timeout pass it on.
    for index, item in enumerate(argv):
        program, arguments = posixpath.basename(item), argv[index + 1:]
        short = [value for value in arguments if value.startswith("-") and not value.startswith("--")]
        if ((program in SHELLS and any("c" in value for value in short))
                or (re.fullmatch(r"perl[0-9.]*", program) and any("e" in value.lower() for value in short))
                or (program in ("node", "nodejs")
                    and any(value.partition("=")[0] in NODE_CODE_OPTIONS for value in arguments))
                or (program == "env" and (any("S" in value for value in short)
                    or any(value.partition("=")[0] == "--split-string" for value in arguments)))):
            return "inline"
        if re.fullmatch(r"python[0-9.]*", program) and (result := python_code(arguments)):
            return result
    return None


def validate_argv(argv, *, healthcheck=False):
    argv = shlex.split(argv) if isinstance(argv, str) else [str(item) for item in argv or []]
    if healthcheck:
        if not argv or argv[0] == "NONE":
            return
        argv = ["sh", "-c", *argv[1:]] if argv[0] == "CMD-SHELL" else argv[1:] if argv[0] == "CMD" else argv
    previous = ""
    for item in argv:
        name, separator, value = item.partition("=")
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and unsafe_variable(name, value):
            raise Refused(IMPORT_REFUSED)
        if (previous not in DATA_OPTIONS and not (separator and name in DATA_OPTIONS)
                and (mounted(item) or MOUNT_REFERENCE.search(item))):
            raise Refused(CODE_REFUSED)
        previous = item
    code = interpreter_code(argv)
    if code == "module":
        raise Refused(CODE_REFUSED)
    # A health probe needs inline Python; it still cannot name a mount (checked above).
    if code == "inline" and not (healthcheck and re.fullmatch(r"python[0-9.]*", posixpath.basename(argv[0]))):
        raise Refused(INLINE_REFUSED)


def validate_runtime(unit):
    # Legacy host-mounted extensions would bypass the verified image entirely.
    mounts = [(mount["target"], mount.get("source", "")) for mount in unit.get("volumes") or []]
    # Compose secrets/configs are file mounts too; a config defaults to /<name>.
    for kind, default in (("secrets", "/run/secrets/"), ("configs", "/")):
        for item in unit.get(kind) or []:
            item = {"source": item} if isinstance(item, str) else item
            target = item.get("target") or item["source"]
            mounts.append((target if target.startswith("/") else default + target, item["source"]))
    for target, source in mounts:
        target = posixpath.normpath(target)
        if (not target.startswith("/") or not mounted(target)
                or target.endswith(CODE_SUFFIXES) or str(source).endswith(CODE_SUFFIXES)):
            raise Refused(CODE_REFUSED)
    environment = unit.get("environment") or {}
    if isinstance(environment, list):
        environment = dict(item.partition("=")[::2] for item in environment)
    if any(unsafe_variable(name, value) for name, value in environment.items()):
        raise Refused(IMPORT_REFUSED)
    if unit.get("working_dir") and mounted(unit["working_dir"]):
        raise Refused(CODE_REFUSED)
    def split(value):
        return shlex.split(value) if isinstance(value, str) else [str(item) for item in value or []]
    # Without an entrypoint override the command may itself start a program.
    validate_argv(split(unit.get("entrypoint")) + split(unit.get("command")))
    validate_argv((unit.get("healthcheck") or {}).get("test"), healthcheck=True)
    if unit.get("post_start") or unit.get("pre_stop"):
        raise Refused("lifecycle hooks run commands outside the verified image; package them in main first")
    if (any(unit.get(key) for key in ("privileged", "cap_add", "devices", "device_cgroup_rules", "volumes_from", "gpus"))
            or any(UNCONFINED.search(str(option)) for option in unit.get("security_opt") or [])
            or any(str(unit.get(key) or "") == "host" for key in HOST_NAMESPACES)
            or str(unit.get("pid") or "").startswith(("container:", "service:"))):
        raise Refused(PRIVILEGE_REFUSED)


def escaped(value):
    # Resolved Compose values are parsed a second time when applying the snapshot.
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escaped(item) for item in value]
    if isinstance(value, dict):
        return {key: escaped(item) for key, item in value.items()}
    return value


def check_space(directory):
    if shutil.disk_usage(directory).free < MIN_FREE_BYTES:
        raise Refused("at least 15 GiB free is required before building production images")


IMAGE_LOCK = Path("/var/lib/deedkit-production/image-build.lock")


@contextmanager
def image_lock():
    """One tag lookup or build at a time on the production host, whichever tool or state directory asks."""
    if not IMAGE_LOCK.parent.is_dir():
        yield  # CI runners and development machines have no production host state.
        return
    with IMAGE_LOCK.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another image build holds the production image lock") from exc
        yield


def build_recipe(target, sha):
    """The build arguments of a target and a digest that binds an image to them, its Dockerfile and stage."""
    _, dockerfile, stage, _ = BUILDS[target]
    arguments = {"DEEDKIT_SOURCE_SHA": sha}
    if stage:
        arguments["ANYMSG_INSTALL_TELEMETRY"] = "1"
    if target == "http-router":
        arguments["VERSION_REF"] = sha
    return arguments, digest({"dockerfile": dockerfile, "stage": stage, "arguments": arguments})


def names_revision(image, sha, dockerfile, recipe=None):
    config = json.loads(run(["docker", "image", "inspect", image]))[0]["Config"]
    labels = config.get("Labels") or {}
    return (labels.get("org.opencontainers.image.revision") == sha
            and (recipe is None or (labels.get("io.deedkit.production.build") == recipe
                                    and labels.get("org.opencontainers.image.source") == "https://github.com/" + REPOSITORY))
            and (dockerfile != "services/messaging/Dockerfile"
                 or "DEEDKIT_SOURCE_SHA=" + sha in (config.get("Env") or [])))


def build_image(source, sha, target, *, check=False):
    context, dockerfile, stage, smoke = BUILDS[target]
    # A reused tag must carry this exact recipe, not only the revision.
    arguments, recipe = build_recipe(target, sha)
    # A container check builds the working tree; it never shares or reuses production tags.
    tag = f"deedkit-{'check' if check else 'production'}-{target}:{sha}"
    with image_lock():
        # Builds are not reproducible, and retagging can drop the image running containers use
        # (containerd image store), so an existing production tag is reused, never rebuilt.
        existing = [] if check else run(["docker", "image", "ls", "--quiet", "--no-trunc", tag]).split()
        if existing:
            image = existing[0]
            if len(set(existing)) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise Refused("existing image tag is ambiguous; inspect it before preparing this revision")
            if not names_revision(image, sha, dockerfile, recipe):
                raise Refused("existing image tag does not name the candidate source revision and build; "
                              "remove it only after checking that no container uses it")
        else:
            command = ["docker", "build", "--label", "org.opencontainers.image.revision=" + sha,
                       "--label", "org.opencontainers.image.source=https://github.com/" + REPOSITORY,
                       "--label", "io.deedkit.production.build=" + recipe]
            for argument, value in arguments.items():
                command += ["--build-arg", f"{argument}={value}"]
            command += ["--tag", tag, "--file", str(source / dockerfile)] + (["--target", stage] if stage else [])
            run(command + [str(source / context)])
            image = run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise Refused("build did not produce an immutable image ID")
            # Only a fresh build is checked: earlier images keep their rollback path without this variable.
            if dockerfile == "services/messaging/Dockerfile" and not names_revision(image, sha, dockerfile):
                raise Refused("built image does not name the candidate source revision")
    name = f"deedkit-smoke-{target}-{uuid.uuid4().hex[:12]}"
    try:
        isolated = ["docker", "run", "--name", name, "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--entrypoint"]
        if target == "http-router":
            run(isolated + ["/deedkit-release-router", image, "--version"])
        else:
            run(isolated + ["python", image, "-c", smoke])
    except Exception:
        # This name was created by this invocation; never stop a shared runtime.
        subprocess.run(["docker", "stop", "--time", "10", name], capture_output=True, timeout=20)
        raise
    return image


def prepare_router(settings, sha):
    """Build enrollment infrastructure from fetched main, without touching serving units."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refused("an exact lowercase 40-character commit SHA is required")
    verify(sha)
    state = Path(settings["state_directory"])
    check_space(state)
    docker_root = run(["docker", "info", "--format", "{{.DockerRootDir}}"]).strip()
    if not docker_root or not Path(docker_root).is_dir():
        raise Refused("preparation requires the local production Docker engine")
    check_space(docker_root)
    release = state / ("router-" + sha)
    if release.exists():
        raise Refused("router directory already exists; inspect it before a new preparation")
    release.mkdir(mode=0o700)
    source = release / "source"
    run(["git", "init", "--quiet", str(source)])
    run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/" + REPOSITORY + ".git"])
    run(["git", "-C", str(source), "fetch", "--depth=1", "origin", "refs/heads/main"])
    if run(["git", "-C", str(source), "rev-parse", "FETCH_HEAD"]).strip() != sha:
        raise Refused("main moved while retrieving source; prepare the new verified commit")
    run(["git", "-C", str(source), "checkout", "--quiet", "--detach", sha], umask=0o022)
    image = build_image(source, sha, "http-router")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise Refused("router build did not produce an immutable image ID")
    evidence = verify(sha)
    receipt = {"version": 1, "target": "http-router", "sha": sha, "image": image, "ci": evidence}
    write(release / "router-build.json", receipt)
    return receipt


def prepare(settings, sha):
    evidence = verify(sha)
    state = Path(settings["state_directory"])
    check_space(state)
    docker_root = run(["docker", "info", "--format", "{{.DockerRootDir}}"]).strip()
    if not docker_root or not Path(docker_root).is_dir():
        raise Refused("preparation requires the local production Docker engine")
    check_space(docker_root)
    release = state / sha
    if release.exists():
        raise Refused("candidate directory already exists; inspect it before a new preparation")
    release.mkdir(mode=0o700)
    source = release / "source"
    run(["git", "init", "--quiet", str(source)])
    run(["git", "-C", str(source), "remote", "add", "origin", "https://github.com/" + REPOSITORY + ".git"])
    run(["git", "-C", str(source), "fetch", "--depth=1", "origin", "refs/heads/main"])
    if run(["git", "-C", str(source), "rev-parse", "FETCH_HEAD"]).strip() != sha:
        raise Refused("main moved while retrieving source; prepare the new verified commit")
    # Docker COPY preserves checkout modes. Public code must be readable by
    # image UID 10001; private release parents and snapshots retain 0700/0600.
    run(["git", "-C", str(source), "checkout", "--quiet", "--detach", sha], umask=0o022)
    snapshots = []
    for project in settings["projects"]:
        document, running = snapshot(project)
        snapshots.append({"project": project, "before": document, "running": running})
    images = {}
    targets = sorted({target for project in settings["projects"] for target in project["services"].values()})
    for target in targets:
        images[target] = build_image(source, sha, target)
    # Recheck after potentially long image builds. No production command ran yet.
    evidence = verify(sha)
    projects = []
    for item in snapshots:
        project, before = item["project"], item["before"]
        candidate = json.loads(json.dumps(before))
        for service in candidate["services"].values():
            service.pop("build", None)
        for service, target in project["services"].items():
            unit = candidate["services"][service]
            unit["image"] = images[target]
            unit.setdefault("labels", {})["org.opencontainers.image.revision"] = sha
        path = release / (project["name"] + ".json")
        write(path, escaped(candidate))
        # Verify that serializing/re-reading did not change secrets, mounts or ports.
        reread = json.loads(run(["docker", "compose", "--profile", "*", "-f", str(path), "config", "--format", "json"]))
        if reread != candidate:
            raise Refused("resolved Compose snapshot did not round-trip exactly")
        projects.append({"name": project["name"], "file": str(path), "digest": digest(read(path)),
                         "before_digest": digest(before), "running": item["running"]})
        rollback = json.loads(json.dumps(before))
        for unit in rollback["services"].values():
            unit.pop("build", None)
        for service, identity in item["running"].items():
            rollback["services"][service]["image"] = identity["image"]
        write(release / (project["name"] + ".before.json"), escaped(rollback))
    receipt = {"version": 1, "sha": sha, "ci": evidence, "images": images,
               "config_digest": digest(settings), "projects": projects}
    write(release / "prepared.json", receipt)
    return receipt


def apply(settings, sha):
    verify(sha)
    state = Path(settings["state_directory"])
    release = state / sha
    receipt = read(release / "prepared.json")
    if receipt["sha"] != sha or receipt["config_digest"] != digest(settings):
        raise Refused("prepared release does not match the selected production configuration")
    if (release / "attempted.json").exists():
        raise Refused("this release was already attempted; inspect its result before any recovery")
    for target, image in receipt["images"].items():
        actual = json.loads(run(["docker", "image", "inspect", image]))[0]
        if (actual["Id"] != image or actual["Config"].get("Labels", {}).get("org.opencontainers.image.revision") != sha):
            raise Refused("prepared image is missing or has the wrong source revision")
    for project, item in zip(settings["projects"], receipt["projects"], strict=True):
        document, running = snapshot(project)
        if (item["name"] != project["name"] or digest(document) != item["before_digest"]
                or running != item["running"] or digest(read(item["file"])) != item["digest"]):
            raise Refused("production or prepared configuration changed since preparation")
    # Last remote gate immediately before the first mutation, under the host lock.
    evidence = verify(sha)
    write(release / "attempted.json", {"sha": sha, "ci": evidence, "status": "applying"})
    for project, item in zip(settings["projects"], receipt["projects"], strict=True):
        command = ["docker", "compose", "--profile", "*", "--project-name", project["name"], "-f", item["file"]]
        services = list(project["services"])
        # Stop the existing owners before replacements can connect to their sessions.
        run(command + ["stop", *services])
        run(command + ["up", "-d", "--no-build", "--pull", "never", "--no-deps",
                       "--wait", "--wait-timeout", "180", *services])
        for service, target in project["services"].items():
            ids = run(command + ["ps", "-q", service]).split()
            if len(ids) != 1:
                raise Refused("replacement service is missing")
            actual = json.loads(run(["docker", "inspect", ids[0]]))[0]
            if actual["Image"] != receipt["images"][target] or not actual["State"]["Running"]:
                raise Refused("replacement did not start the prepared image")
    result = {"sha": sha, "ci": evidence, "images": receipt["images"], "status": "applied"}
    write(release / "result.json", result)
    write(state / "current.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "prepare", "prepare-router", "apply", "status", "check-image"])
    parser.add_argument("--sha")
    parser.add_argument("--target", choices=sorted(BUILDS))
    parser.add_argument("--config", default="/etc/deedkit/production.json")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "check-image":
            if not args.target:
                raise Refused("select a target for container verification")
            source = Path(__file__).resolve().parents[1]
            sha = run(["git", "-C", str(source), "rev-parse", "HEAD"]).strip()
            result = {"sha": sha, "image": build_image(source, sha, args.target, check=True)}
        elif args.action == "verify":
            result = verify(args.sha or "")
        else:
            settings = config(args.config)
            state = Path(settings["state_directory"])
            with ExitStack() as locks:
                if args.action == "prepare-router":
                    if os.geteuid() != 0:
                        raise Refused("router preparation requires the production host operator")
                    canonical = Path("/var/lib/deedkit-production")
                    locks.enter_context(lock(canonical))
                    if state.resolve() != canonical.resolve():
                        locks.enter_context(lock(state))
                else:
                    locks.enter_context(lock(state))
                if args.action == "status":
                    result = read(state / "current.json") if (state / "current.json").exists() else {"status": "not promoted by this tool"}
                else:
                    if not re.fullmatch(r"[0-9a-f]{40}", args.sha or ""):
                        raise Refused("an exact lowercase 40-character commit SHA is required")
                    result = {"prepare": prepare, "prepare-router": prepare_router, "apply": apply}[args.action](settings, args.sha)
        # Receipts contain revisions/image IDs, never resolved Compose environments.
        if args.action == "prepare":
            result = {key: result[key] for key in ("sha", "ci", "images")}
        print(json.dumps(result, indent=2))
    except Exception as exc:
        message = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        parser.exit(1, "promotion refused: " + message + "\n")


if __name__ == "__main__":
    main()
