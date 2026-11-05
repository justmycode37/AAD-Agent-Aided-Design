"""The ``aad`` command line. Heavy imports stay inside the commands that need them."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__

CLI_CLIENT = {"name": "aad-cli", "supports_ui": False}


class ClientApiError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aad", description="CAD for agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    version = sub.add_parser("version", help="print the installed version")
    version.add_argument("--json", action="store_true", help="machine-readable output")

    # MCP stdio and version retain their existing wire/output contracts.
    mcp = sub.add_parser("mcp", help="run the MCP adapter on stdio")
    mcp.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    install = sub.add_parser("install", help="configure MCP clients and start AAD")
    install.add_argument("--client", action="append", nargs="+", metavar="ID", help="install only these clients")
    install.add_argument("--dry-run", action="store_true", help="show changes without writing or starting services")
    install.add_argument("--no-prewarm", action="store_true", help="skip import pre-warming")
    install.add_argument("--no-doctor", action="store_true", help="skip the diagnostic smoke test")
    install.add_argument("--json", action="store_true", help="machine-readable output")

    uninstall = sub.add_parser("uninstall", help="remove AAD from MCP clients")
    uninstall.add_argument("--client", action="append", nargs="+", metavar="ID", help="uninstall only these clients")
    uninstall.add_argument("--dry-run", action="store_true", help="show changes without writing or stopping services")
    uninstall.add_argument("--purge", action="store_true", help="also remove AAD_HOME")
    uninstall.add_argument("--json", action="store_true", help="machine-readable output")

    doctor = sub.add_parser("doctor", help="check the local AAD installation")
    doctor.add_argument("--json", action="store_true", help="machine-readable output")

    config = sub.add_parser("config", help="read or set a configuration value")
    config.add_argument("setting", nargs="?", metavar="KEY[=VALUE]")
    config.add_argument("--json", action="store_true", help="machine-readable output")

    daemon = sub.add_parser("daemon", help="manage the local aadd daemon")
    daemon.add_argument("action", nargs="?", choices=("run", "start", "stop", "status"), default="run")
    daemon.add_argument("--json", action="store_true", help="machine-readable output")

    build = sub.add_parser("build", help="build a project revision")
    build.add_argument("project")
    build.add_argument("file", nargs="?", help="model.py source file")
    build.add_argument("--param", action="append", default=[], metavar="K=V", help="override a declared parameter")
    build.add_argument("--preview", action="store_true", help="request a build preview")
    build.add_argument("--create", action="store_true", help="create the project before building")
    build.add_argument("--timeout", type=float, metavar="S", help="seconds before the build worker is killed (default 60, max 600)")
    build.add_argument(
        "--kind",
        choices=("part", "assembly", "2d"),
        default="part",
        help="project kind when used with --create",
    )
    build.add_argument("--json", action="store_true", help="machine-readable output")

    show = sub.add_parser("show", help="show a project revision")
    show.add_argument("project")
    show.add_argument("--rev", type=int)
    show.add_argument("--json", action="store_true", help="machine-readable output")

    export = sub.add_parser("export", help="export a project revision")
    export.add_argument("project")
    export.add_argument("--format", action="append", nargs="+", required=True, metavar="F", help="format(s) to export")
    export.add_argument("--rev", type=int)
    export.add_argument("--allow-unverified", action="store_true")
    export.add_argument("--json", action="store_true", help="machine-readable output")

    update = sub.add_parser("update", help="show or run the uv tool upgrade command")
    update.add_argument("--run", action="store_true", help="run the upgrade")
    update.add_argument("--json", action="store_true", help="machine-readable output")

    serve = sub.add_parser("serve", help="run remote AAD mode over Streamable HTTP")
    serve.add_argument("--http", action="store_true", help="serve MCP at /mcp")
    serve.add_argument("--host", default="127.0.0.1", help="bind address, default loopback")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--public-url", help="https URL of the TLS proxy, required off loopback")
    serve.add_argument("--add-user", metavar="NAME", help="create a user or rotate their token, and print it once")
    serve.add_argument("--remove-user", metavar="NAME", help="revoke a user; their projects stay on disk")
    serve.add_argument("--list-users", action="store_true")
    serve.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _finish(payload: dict[str, Any], json_mode: bool, human: str | None = None) -> int:
    result = dict(payload)
    result["ok"] = bool(result.get("ok"))
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str))
    else:
        message = human or str(result.get("message") or result.get("error") or ("OK" if result["ok"] else "AAD command failed."))
        print(message)
    return 0 if result["ok"] else 1


def _clients_api() -> Any:
    try:
        return importlib.import_module(".clients", package="aad_platform")
    except ModuleNotFoundError as exc:
        if exc.name in {"aad_platform.clients", "aad_platform"}:
            raise ClientApiError("Client configuration writers are not available in this AAD build.") from exc
        raise ClientApiError(f"Could not load client configuration writers: {exc}") from exc


def _selected_ids(groups: list[list[str]] | None) -> list[str] | None:
    if not groups:
        return None
    return list(dict.fromkeys(client_id for group in groups for client_id in group))


def _status_dict(status: Any) -> dict[str, Any]:
    return {
        "id": status.id,
        "name": status.name,
        "detected": bool(status.detected),
        "config_path": str(status.config_path) if status.config_path is not None else None,
        "installed": bool(status.installed),
        "restart_hint": status.restart_hint,
    }


def _write_result(result: Any) -> dict[str, Any]:
    return {
        "id": result.id,
        "action": result.action,
        "config_path": str(result.config_path) if result.config_path is not None else None,
        "backup_path": str(result.backup_path) if result.backup_path is not None else None,
        "diff": result.diff,
        "message": result.message,
    }


def _write_human(writes: list[dict[str, Any]], heading: str) -> str:
    lines = [heading]
    for item in writes:
        lines.append(f"{item['id']}: {item['action']}")
        if item.get("diff"):
            lines.append(str(item["diff"]).rstrip())
        elif item.get("message"):
            lines.append(str(item["message"]))
    return "\n".join(lines)


def _script_entry() -> tuple[str, tuple[str, ...]]:
    """Return the absolute console script, or a module invocation as a safe fallback."""
    # A different AAD on PATH can belong to another Python environment.
    candidates = [sys.argv[0]]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.name.lower() not in {"aad", "aad.exe", "aad-script.py"}:
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return str(resolved), ("mcp",)
    return sys.executable, ("-m", "aad_platform.cli", "mcp")


def _install(args: argparse.Namespace) -> int:
    try:
        clients = _clients_api()
        statuses = clients.statuses()
        available = set(clients.CLIENT_IDS)
        selected = _selected_ids(args.client)
        if selected is None:
            selected = [status.id for status in statuses if status.detected]
        unknown = [client_id for client_id in selected if client_id not in available]
        if unknown:
            return _finish({"ok": False, "error": f"Unknown client id(s): {', '.join(unknown)}", "client_ids": sorted(available)}, args.json)
    except Exception as exc:
        return _finish({"ok": False, "error": str(exc)}, args.json)

    entry_command, entry_args = _script_entry()
    entry = clients.ServerEntry(command=entry_command, args=entry_args)
    writes: list[dict[str, Any]] = []
    try:
        for client_id in selected:
            writes.append(_write_result(clients.install(client_id, entry, dry_run=args.dry_run)))
    except Exception as exc:
        return _finish({"ok": False, "error": f"Client install failed: {exc}", "clients": writes}, args.json)

    if args.dry_run:
        ok = all(item["action"] not in {"error", "failed"} for item in writes)
        message = "Dry run complete; no clients detected." if not writes else "Dry run complete."
        return _finish({"ok": ok, "dry_run": True, "entry": {"command": entry.command, "args": list(entry.args)}, "clients": writes, "message": message}, args.json, _write_human(writes, message))

    prewarm_result: dict[str, Any] | None = None
    if not args.no_prewarm:
        try:
            from .prewarm import run_prewarm

            prewarm_result = run_prewarm(lambda message: print(message, file=sys.stderr, flush=True))
        except Exception as exc:
            prewarm_result = {"ok": False, "error": f"Pre-warm failed: {type(exc).__name__}: {exc}"}

    daemon_result: dict[str, Any] | None = None
    daemon_error: str | None = None
    try:
        from .daemon_ctl import ensure_running

        daemon_result = ensure_running()
    except Exception as exc:
        daemon_error = f"Could not start aadd: {type(exc).__name__}: {exc}"

    doctor_result: dict[str, Any] | None = None
    if not args.no_doctor:
        try:
            from .doctor import run_doctor

            doctor_result = run_doctor()
        except Exception as exc:
            doctor_result = {"ok": False, "checks": [], "error": f"Doctor failed: {type(exc).__name__}: {exc}"}

    status_by_id = {status.id: status for status in statuses}
    restart_hints = [
        {"id": client_id, "hint": str(status_by_id[client_id].restart_hint)}
        for client_id in selected
        if client_id in status_by_id and status_by_id[client_id].restart_hint
    ]
    client_ok = all(item["action"] not in {"error", "failed"} for item in writes)
    ok = client_ok and daemon_error is None
    if prewarm_result is not None:
        ok = ok and bool(prewarm_result.get("ok"))
    if doctor_result is not None:
        ok = ok and bool(doctor_result.get("ok"))
    message_parts = [f"Configured {len(writes)} client(s)."]
    if daemon_error:
        message_parts.append(daemon_error)
    if restart_hints:
        message_parts.append("New session needed: " + "; ".join(f"{item['id']}: {item['hint']}" for item in restart_hints))
    else:
        message_parts.append("No client needs a new session.")
    payload = {
        "ok": ok,
        "dry_run": False,
        "clients": writes,
        "restart_hints": restart_hints,
        "prewarm": prewarm_result,
        "daemon": daemon_result,
        "daemon_error": daemon_error,
        "doctor": doctor_result,
        "message": " ".join(message_parts),
    }
    return _finish(payload, args.json)


def _paths_overlap(first: Path, second: Path) -> bool:
    if first == second or first.is_relative_to(second) or second.is_relative_to(first):
        return True
    # samefile also recognizes differently cased paths on macOS volumes.
    for ancestor in (first, *first.parents):
        if ancestor.exists() and second.exists() and ancestor.samefile(second):
            return True
    for ancestor in (second, *second.parents):
        if ancestor.exists() and first.exists() and ancestor.samefile(first):
            return True
    return False


def _purge_aad_home() -> tuple[bool, str]:
    from . import paths

    target = paths.aad_home()
    project_root = paths.default_projects_root()
    try:
        target_resolved = target.resolve()
        project_resolved = project_root.resolve()
        if _paths_overlap(target_resolved, project_resolved):
            return False, f"Refusing to purge {target}: it overlaps ~/AAD, which contains project data."
        if target.is_symlink():
            return False, f"Refusing to purge symlinked AAD_HOME {target}."
        if any((target / "backups").glob("*/manifest.json")):
            return False, "Refusing to purge AAD_HOME while client backup manifests remain. Resolve their uninstall results first."
        if target.exists():
            shutil.rmtree(target)
        return True, f"Removed AAD_HOME at {target}."
    except OSError as exc:
        return False, f"Could not purge AAD_HOME {target}: {exc}"


def _uninstall(args: argparse.Namespace) -> int:
    writes: list[dict[str, Any]] = []
    api_error: str | None = None
    try:
        clients = _clients_api()
        statuses = clients.statuses()
        available = set(clients.CLIENT_IDS)
        selected = _selected_ids(args.client)
        if selected is None:
            selected = [status.id for status in statuses if status.installed]
        unknown = [client_id for client_id in selected if client_id not in available]
        if unknown:
            raise ClientApiError(f"Unknown client id(s): {', '.join(unknown)}")
        for client_id in selected:
            writes.append(_write_result(clients.uninstall(client_id, dry_run=args.dry_run)))
    except Exception as exc:
        api_error = str(exc)

    if api_error:
        return _finish({"ok": False, "clients": writes, "client_error": api_error, "message": api_error}, args.json)

    stopped: bool | None = None
    stop_error: str | None = None
    if not args.dry_run:
        try:
            from .daemon_ctl import stop

            stopped = stop()
        except Exception as exc:
            stopped = False
            stop_error = f"Could not stop aadd: {type(exc).__name__}: {exc}"

    purged: bool | None = None
    purge_message: str | None = None
    client_ok = api_error is None and all(item["action"] not in {"error", "failed", "skipped"} for item in writes)
    if args.purge and not args.dry_run:
        if client_ok and stopped is True:
            purged, purge_message = _purge_aad_home()
        else:
            purged, purge_message = False, "AAD_HOME was kept because client uninstall or daemon shutdown failed."

    ok = client_ok and (args.dry_run or stopped is True) and (not args.purge or args.dry_run or purged is True)
    message_parts = ["Dry run complete." if args.dry_run else f"Uninstalled from {len(writes)} client(s)."]
    if api_error:
        message_parts.append(api_error)
    if stop_error:
        message_parts.append(stop_error)
    if purge_message:
        message_parts.append(purge_message)
    if args.dry_run and args.purge:
        message_parts.append("AAD_HOME would be removed; ~/AAD is never purged.")
    payload = {"ok": ok, "dry_run": args.dry_run, "clients": writes, "client_error": api_error, "daemon_stopped": stopped, "purged": purged, "message": " ".join(message_parts)}
    return _finish(payload, args.json, _write_human(writes, payload["message"]) if args.dry_run else None)


def _config_command(args: argparse.Namespace) -> int:
    from . import config

    try:
        if args.setting is None:
            values = config.load()
            return _finish({"ok": True, "values": values}, args.json, "\n".join(f"{key} = {value}" for key, value in sorted(values.items())))
        if "=" in args.setting:
            key, value = args.setting.split("=", 1)
            if not key:
                raise config.ConfigError("Configuration key cannot be empty.")
            config.set_value(key, value)
            return _finish({"ok": True, "key": key, "value": value, "updated": True}, args.json, f"{key} = {value}")
        value = config.get(args.setting)
        return _finish({"ok": True, "key": args.setting, "value": value}, args.json, f"{args.setting} = {value}")
    except config.ConfigError as exc:
        return _finish({"ok": False, "error": str(exc)}, args.json)


def _daemon_command(args: argparse.Namespace) -> int:
    if args.action == "run":
        from .daemon.server import main as daemon_main

        result = daemon_main()
        if args.json:
            return _finish({"ok": result == 0, "action": "run", "returncode": result}, True)
        return result
    from . import daemon_ctl

    try:
        if args.action == "start":
            status = daemon_ctl.ensure_running()
            ok = bool(status.get("running") and status.get("compatible"))
            return _finish({"ok": ok, "action": "start", **status}, args.json, f"aadd running (pid {status.get('pid')}, AAD {status.get('version')}).")
        if args.action == "status":
            status = daemon_ctl.status()
            ok = bool(status.get("running") and status.get("compatible"))
            return _finish({"ok": ok, "action": "status", **status}, args.json, f"aadd {'running' if ok else 'not running'}; pid {status.get('pid')}, version {status.get('version')}, socket {status.get('socket')}, HTTP port {status.get('http_port')}.")
        stopped = daemon_ctl.stop()
        return _finish({"ok": bool(stopped), "action": "stop", "stopped": bool(stopped)}, args.json, "aadd stopped." if stopped else "aadd could not be stopped safely.")
    except Exception as exc:
        return _finish({"ok": False, "action": args.action, "error": f"{type(exc).__name__}: {exc}"}, args.json)


def _parse_param(value: str) -> tuple[str, Any]:
    if "=" not in value:
        raise ValueError(f"Parameter {value!r} must use K=V.")
    key, raw = value.split("=", 1)
    if not key:
        raise ValueError("Parameter name cannot be empty.")
    if not raw:
        return key, ""
    try:
        return key, int(raw)
    except ValueError:
        try:
            return key, float(raw)
        except ValueError:
            return key, raw


def _rpc_sequence(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    from . import paths
    from .rpc import RpcClient

    async def run_calls() -> list[dict[str, Any]]:
        client = await RpcClient.connect(paths.socket_path())
        try:
            replies: list[dict[str, Any]] = []
            for method, params in calls:
                timeout_s = 125.0 if method == "aad_build" else 60.0
                reply = await asyncio.wait_for(client.call(method, params), timeout=timeout_s)
                replies.append(reply if isinstance(reply, dict) else {"ok": False, "error": f"Invalid reply from {method}: {reply!r}"})
            return replies
        finally:
            await client.close()

    return asyncio.run(run_calls())


def _remote_calls(calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        from .daemon_ctl import ensure_running

        status = ensure_running()
        if not status.get("compatible"):
            return None, "aadd did not complete a compatible hello handshake."
        return _rpc_sequence(calls), None
    except Exception as exc:
        return None, f"Daemon RPC failed: {type(exc).__name__}: {exc}"


def _structured_reply(reply: dict[str, Any]) -> dict[str, Any]:
    structured = reply.get("structured")
    if isinstance(structured, dict):
        return structured
    if "ok" in reply:
        return reply
    return {"ok": False, "error": reply.get("error", "Daemon returned no structured result.")}


def _remote_command(args: argparse.Namespace) -> int:
    calls: list[tuple[str, dict[str, Any]]] = []
    if args.command == "build":
        source: str | None = None
        if args.file:
            try:
                source = Path(args.file).expanduser().read_text(encoding="utf-8")
            except OSError as exc:
                return _finish({"ok": False, "error": f"Could not read {args.file}: {exc}"}, args.json)
        common = {"project": args.project, "client": dict(CLI_CLIENT)}
        if args.create:
            new_params: dict[str, Any] = {"name": args.project, "kind": args.kind, "client": dict(CLI_CLIENT)}
            if source is not None:
                new_params["source"] = source
            calls.append(("aad_new", new_params))
        params: dict[str, Any] = {**common}
        if source is not None:
            params["source"] = source
        if args.param:
            try:
                params["params"] = dict(_parse_param(value) for value in args.param)
            except ValueError as exc:
                return _finish({"ok": False, "error": str(exc)}, args.json)
        if args.preview:
            params["preview"] = True
        if args.timeout is not None:
            params["timeout_s"] = args.timeout
        calls.append(("aad_build", params))
    elif args.command == "show":
        params = {"project": args.project, "client": dict(CLI_CLIENT)}
        if args.rev is not None:
            params["rev"] = args.rev
        calls.append(("aad_show", params))
    else:
        formats: list[str] = []
        for group in args.format:
            for value in group:
                formats.extend(piece for piece in value.split(",") if piece)
        params = {"project": args.project, "formats": formats, "client": dict(CLI_CLIENT)}
        if args.rev is not None:
            params["rev"] = args.rev
        if args.allow_unverified:
            params["allow_unverified"] = True
        calls.append(("aad_export", params))

    if args.command == "build" and args.create:
        created, create_error = _remote_calls(calls[:1])
        if create_error or not created:
            return _finish({"ok": False, "error": create_error or "Project creation returned no response."}, args.json)
        created_result = _structured_reply(created[0])
        if created_result.get("ok") is not True:
            if args.json:
                return _finish(created_result, True)
            return _finish({"ok": False, "message": str(created[0].get("text") or created_result.get("summary") or "Project creation failed.")}, False)
        replies, error = _remote_calls(calls[1:])
    else:
        replies, error = _remote_calls(calls)
    if error or replies is None:
        return _finish({"ok": False, "error": error or "No daemon response."}, args.json)
    reply = replies[-1]
    structured = _structured_reply(reply)
    ok = structured.get("ok") is True
    if args.json:
        return _finish(structured, True)
    message = str(reply.get("text") or structured.get("summary") or structured.get("error") or "")
    if args.command == "show" and ok:
        viewer_info = structured.get("viewer")
        url = viewer_info.get("url") if isinstance(viewer_info, dict) else structured.get("viewer_url")
        if url:
            message = f"{message}\nViewer: {url}" if message else f"Viewer: {url}"
    return _finish({"ok": ok, "message": message}, False)


def _update(args: argparse.Namespace) -> int:
    command = ["uv", "tool", "upgrade", "aad-design"]
    if not args.run:
        return _finish({"ok": True, "command": " ".join(command), "ran": False}, args.json, "uv tool upgrade aad-design")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return _finish({"ok": False, "command": " ".join(command), "ran": True, "error": str(exc)}, args.json)
    payload = {"ok": result.returncode == 0, "command": " ".join(command), "ran": True, "returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    human = "\n".join(value for value in (payload["stdout"], payload["stderr"]) if value) or "uv tool upgrade aad-design"
    return _finish(payload, args.json, human)


def _serve(args: argparse.Namespace) -> int:
    from . import remote

    try:
        if args.add_user:
            token = remote.add_user(args.add_user)
            return _finish({"ok": True, "user": args.add_user, "token": token}, args.json, f"Token for {args.add_user}, shown once:\n{token}")
        if args.remove_user:
            removed = remote.remove_user(args.remove_user)
            message = f"Revoked {args.remove_user}." if removed else f"No user {args.remove_user}."
            return _finish({"ok": removed, "message": message}, args.json, message)
        if args.list_users:
            names = sorted(remote.load_users())
            return _finish({"ok": True, "users": names}, args.json, "\n".join(names) or "No users.")
        if not args.http:
            message = "Nothing to do. Use --http, --add-user, --remove-user or --list-users."
            return _finish({"ok": False, "error": message}, args.json, message)
        remote.serve(host=args.host, port=args.port, public_url=args.public_url)
        return 0
    except remote.RemoteError as exc:
        return _finish({"ok": False, "error": str(exc)}, args.json, str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "version":
        # Preserve the existing version output contract.
        print(json.dumps({"version": __version__}) if args.json else __version__)
        return 0
    if args.command == "mcp":
        from .adapter import main as adapter_main

        return adapter_main()
    if args.command == "install":
        return _install(args)
    if args.command == "uninstall":
        return _uninstall(args)
    if args.command == "doctor":
        try:
            from .doctor import run_doctor

            result = run_doctor()
        except Exception as exc:
            result = {"ok": False, "checks": [], "error": f"Doctor failed: {type(exc).__name__}: {exc}"}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str))
        else:
            for check in result.get("checks", []):
                print(f"{check['status'].upper()} {check['id']}: {check['message']}")
                if check.get("fix") and check["status"] == "fail":
                    print(f"  Fix: {check['fix']}")
            if result.get("error"):
                print(result["error"])
        return 0 if result.get("ok") else 1
    if args.command == "config":
        return _config_command(args)
    if args.command == "daemon":
        return _daemon_command(args)
    if args.command in {"build", "show", "export"}:
        return _remote_command(args)
    if args.command == "update":
        return _update(args)
    if args.command == "serve":
        return _serve(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
