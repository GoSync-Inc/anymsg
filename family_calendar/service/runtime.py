"""Owner-run local PostgreSQL and macOS service lifecycle, without secrets in argv."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import sys
import time

from family_calendar.errors import CalendarError
from family_calendar.private_files import private_dir, read_private, write_private
from .store import Store

CONTAINER = "family-calendar-postgres"


def launch_configuration(repo, role):
    repo = Path(repo).resolve()
    if role not in ("gateway", "worker"):
        raise CalendarError("invalid_role")
    root = repo / ".local" / "service"
    return {
        "Label": "com.family-calendar." + role,
        "ProgramArguments": [
            str(repo / ".venv/bin/python"),
            "-m",
            "family_calendar.cli",
            "--home",
            str(repo / ".local"),
            "service",
            "serve" if role == "gateway" else "worker",
        ],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ExitTimeOut": 10,
        "StandardOutPath": str(root / (role + ".stdout.log")),
        "StandardErrorPath": str(root / (role + ".stderr.log")),
    }


def command(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise CalendarError("local_runtime_command_failed")
    return result


def bootstrap(repo, db_port=13231, api_port=13232):
    root = private_dir(repo / ".local" / "service")
    config_path = root / "runtime.json"
    info = command(["docker", "inspect", CONTAINER], check=False)
    if info.returncode == 0:
        metadata = json.loads(info.stdout)[0]
        if metadata["Config"]["Labels"].get("service") != "family-calendar" or not config_path.exists():
            raise CalendarError("existing_container_not_owned")
        config = json.loads(read_private(config_path))
        command(["docker", "start", CONTAINER])
    else:
        command(["docker", "info", "--format", "{{.ServerVersion}}"])
        if config_path.exists():
            raise CalendarError("database_container_missing_restore_required")
        password = secrets.token_urlsafe(40)
        write_private(
            root / "postgres.env",
            (
                "POSTGRES_USER=fcs_owner\nPOSTGRES_DB=family_calendar\nPOSTGRES_PASSWORD=" + password + "\n"
            ).encode(),
        )
        command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINER,
                "--label",
                "service=family-calendar",
                "--restart",
                "unless-stopped",
                "--env-file",
                str(root / "postgres.env"),
                "-p",
                f"127.0.0.1:{db_port}:5432",
                "-v",
                "family-calendar-postgres:/var/lib/postgresql",
                "postgres:18",
            ]
        )
        config = {
            "dsn": f"postgresql://fcs_owner:{password}@127.0.0.1:{db_port}/family_calendar",
            "port": api_port,
        }
        write_private(config_path, json.dumps(config).encode())
    for _ in range(60):
        if (
            command(
                ["docker", "exec", CONTAINER, "pg_isready", "-U", "fcs_owner", "-d", "family_calendar"],
                check=False,
            ).returncode
            == 0
        ):
            break
        time.sleep(0.5)
    else:
        raise CalendarError("database_not_ready")
    store = Store(config["dsn"])
    from .cli import db_roles

    try:
        store.migrate()
        db_roles(store, root, config)
    finally:
        store.close()
    return {"database_ready": True, "schema_ready": True, "separate_db_roles": True}


def agents(repo, action):
    if sys.platform != "darwin":
        raise CalendarError("macos_required")
    root = private_dir(repo / ".local" / "service")
    launch_dir = Path.home() / "Library" / "LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    results = []
    for role in ("gateway", "worker"):
        config = launch_configuration(repo, role)
        target = launch_dir / (config["Label"] + ".plist")
        label = domain + "/" + config["Label"]
        if target.exists() or target.is_symlink():
            old = plistlib.loads(read_private(target))
            if old.get("WorkingDirectory") != str(repo.resolve()):
                raise CalendarError("launch_agent_not_owned")
        elif action != "install":
            raise CalendarError("launch_agent_not_installed")
        if action == "install":
            for stream in ("stdout", "stderr"):
                path = root / (role + "." + stream + ".log")
                if not path.exists():
                    write_private(path, b"")
            data = plistlib.dumps(config)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
        loaded = command(["launchctl", "print", label], check=False)
        if action in ("install", "start") and loaded.returncode != 0:
            command(["launchctl", "bootstrap", domain, str(target)])
        if action == "stop" and loaded.returncode == 0:
            command(["launchctl", "bootout", label])
        state = command(["launchctl", "print", label], check=False)
        results.append(
            {"role": role, "loaded": state.returncode == 0, "running": "state = running" in state.stdout}
        )
    return {"agents": results}


def main():
    p = argparse.ArgumentParser(description="Family Calendar owned local runtime")
    p.add_argument("action", choices=["bootstrap", "install", "start", "stop", "status"])
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--db-port", type=int, default=13231)
    p.add_argument("--api-port", type=int, default=13232)
    a = p.parse_args()
    repo = a.repo.resolve()
    try:
        result = bootstrap(repo, a.db_port, a.api_port) if a.action == "bootstrap" else agents(repo, a.action)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": e.code if isinstance(e, CalendarError) else "local_runtime_unavailable"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
