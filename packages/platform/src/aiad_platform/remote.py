"""`aiad serve --http`: MCP over Streamable HTTP, for clients that cannot reach a local process.

Each user gets a private home and their own daemon, so projects, scenes and config never mix.
Requests authenticate with a bearer token. Only token hashes are stored. This is not OS-user
separation: every daemon runs as the account that started the server, and the worker sandbox
is the boundary around model code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import paths

DEFAULT_PORT = 8765
USER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
SCOPE = "aiad"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class RemoteError(ValueError):
    pass


def _users_path() -> Path:
    return paths.aiad_home() / "remote_users.json"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_users() -> dict[str, str]:
    try:
        data = json.loads(_users_path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RemoteError(f"{_users_path()} cannot be read: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _save_users(users: dict[str, str]) -> None:
    paths.ensure_private_dir(paths.aiad_home())
    tmp = _users_path().with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(users, fh, indent=2)
    os.replace(tmp, _users_path())


def add_user(name: str) -> str:
    """Create a user or rotate their token. Returns the token, which is never stored in clear."""
    if not USER_RE.fullmatch(name or ""):
        raise RemoteError("A user name is lowercase letters, digits, '_' or '-', at most 32 characters.")
    token = secrets.token_urlsafe(32)
    _save_users({**load_users(), name: _hash(token)})
    return token


def remove_user(name: str) -> bool:
    """Revoke access. Their projects stay on disk under users/<name>."""
    users = load_users()
    if name not in users:
        return False
    del users[name]
    _save_users(users)
    return True


def user_for_token(token: str) -> str | None:
    digest = _hash(token)
    match = None
    # Check every entry, so the time taken does not depend on which user matched.
    for user, stored in load_users().items():
        if secrets.compare_digest(digest, stored):
            match = user
    return match


def worker_sandbox_active() -> bool:
    """Whether public builds run inside the OS sandbox. S1 sets this once they do."""
    try:
        from aiad.engine import jobs
    except ImportError:
        return False
    return getattr(jobs, "SANDBOXED", False) is True


def check_bind(host: str, public_url: str | None) -> None:
    """Refuse a network-facing server unless it is behind TLS and model code is sandboxed."""
    if host in LOOPBACK:
        return
    if not public_url or urlparse(public_url).scheme != "https":
        raise RemoteError(
            f"Binding to {host} needs --public-url https://... with TLS terminated in front of AIAD, "
            "because bearer tokens must not cross a network in clear."
        )
    if not worker_sandbox_active():
        raise RemoteError(
            f"Refusing to bind to {host}: builds in this install do not run in the OS sandbox, so anyone "
            "with a token could run arbitrary code as this account. Use a loopback address."
        )


class UserLinks:
    """One daemon per user, each with a private HOME and AIAD_HOME."""

    def __init__(self) -> None:
        self._links: dict[str, Any] = {}

    def home(self, user: str) -> Path:
        if not USER_RE.fullmatch(user):
            raise RemoteError(f"Invalid user {user!r}.")
        home = paths.aiad_home() / "users" / user
        paths.ensure_private_dir(paths.aiad_home() / "users")
        paths.ensure_private_dir(home)
        return home

    def link(self, user: str) -> Any:
        from .adapter import DaemonLink

        if user not in self._links:
            home = self.home(user)
            env = {**os.environ, "HOME": str(home), "AIAD_HOME": str(home / ".aiad"), "AIAD_REMOTE": "1"}
            self._links[user] = DaemonLink(env=env)
        return self._links[user]


def build_app(*, host: str, port: int, public_url: str | None = None) -> Any:
    from mcp.server.auth.middleware.auth_context import get_access_token
    from mcp.server.auth.provider import AccessToken
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.transport_security import TransportSecuritySettings

    from .adapter import build_server

    class Verifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            user = user_for_token(token)
            if user is None:
                return None
            return AccessToken(token=token, client_id=user, subject=user, scopes=[SCOPE])

    links = UserLinks()

    def current_link() -> Any:
        access = get_access_token()
        if access is None:  # The auth middleware runs first, so this means a server bug.
            raise ConnectionError("No authenticated user for this request.")
        return links.link(access.client_id)

    base_url = (public_url or f"http://{host}:{port}").rstrip("/")
    security = None
    if public_url:
        parsed = urlparse(public_url)
        security = TransportSecuritySettings(
            allowed_hosts=[parsed.netloc],
            allowed_origins=[f"{parsed.scheme}://{parsed.netloc}"],
        )
    server = build_server(link_for=current_link, use_roots=False)
    return server.streamable_http_app(
        host=host,
        json_response=True,
        token_verifier=Verifier(),
        transport_security=security,
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            required_scopes=[SCOPE],
            validate_token_resource=False,
        ),
    )


def serve(*, host: str, port: int, public_url: str | None = None) -> None:
    import uvicorn

    check_bind(host, public_url)
    if not load_users():
        raise RemoteError("Remote mode requires authentication. Create a user first: aiad serve --add-user NAME")
    uvicorn.run(build_app(host=host, port=port, public_url=public_url), host=host, port=port, log_config=None)
