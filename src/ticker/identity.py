from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote as urlquote, urljoin
from urllib.request import Request, urlopen


DEFAULT_API_URL = "http://127.0.0.1:8000" if os.getenv("ENV") == "dev" else "https://35-207-0-56.sslip.io"


class IdentityError(Exception):
    """A device identity could not be created or persisted."""


class WatchlistVersionMismatch(IdentityError):
    def __init__(self, version: int):
        super().__init__(f"watchlist version changed to {version}")
        self.version = version


def config_directory() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    return Path(root) / "ticker" if root else Path.home() / ".config" / "ticker"


class DeviceIdentity:
    def __init__(
        self,
        path: Path | None = None,
        api_url: str | None = None,
        opener: Callable[..., Any] = urlopen,
    ):
        self.path = path or config_directory() / "credentials.json"
        self.api_url = (api_url or os.environ.get("TICKER_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.opener = opener

    def token(self) -> str | None:
        environment_token = os.environ.get("TICKER_TOKEN", "").strip()
        if environment_token:
            return environment_token
        try:
            payload = json.loads(self.path.read_text())
            token = payload.get("token")
            return token.strip() if isinstance(token, str) and token.strip() else None
        except (OSError, ValueError, TypeError):
            return None

    def ensure_token(self) -> str:
        token = self.token()
        if token:
            return token

        token = self._register()
        self._save(token)
        return token

    def sync_watchlist(self, symbols: list[str]) -> bool:
        token = self.token()
        if not token:
            return False

        lists = self._json_request("v1/lists", token)
        watchlists = lists.get("lists")
        if not isinstance(watchlists, list):
            raise IdentityError("list endpoint returned no lists")
        default = next(
            (item for item in watchlists if isinstance(item, dict) and item.get("is_default")),
            None,
        )
        if default is None or not default.get("id") or default.get("version") is None:
            raise IdentityError("default list was not found")

        list_id = str(default["id"])
        version = int(default["version"])
        self._replace_watchlist(list_id, version, symbols, token)
        return True

    def community_members(self, limit: int = 50) -> list[dict[str, Any]]:
        token = self.token()
        if not token:
            return []
        payload = self._json_request(f"v1/community/members?limit={min(max(limit, 1), 50)}", token)
        members = payload.get("members")
        return [member for member in members if isinstance(member, dict)] if isinstance(members, list) else []

    def community_profile(self, handle: str) -> dict[str, Any]:
        token = self.token()
        if not token:
            raise IdentityError("device token is missing")
        return self._json_request(f"v1/u/{urlquote(handle, safe='')}", token)

    def join_community(self, display_name: str) -> dict[str, Any]:
        token = self.token()
        if not token:
            raise IdentityError("device token is missing")
        body = json.dumps({"display_name": display_name}).encode()
        return self._json_request("v1/community/join", token, data=body, method="POST")

    def send_feedback(self, message: str) -> dict[str, Any]:
        token = self.token()
        if not token:
            raise IdentityError("device token is missing")
        body = json.dumps({"msg": message}).encode()
        return self._json_request("v1/feedback", token, data=body, method="POST")

    def _replace_watchlist(
        self,
        list_id: str,
        version: int,
        symbols: list[str],
        token: str,
    ) -> None:
        body = json.dumps(
            {"items": [{"symbol": symbol.upper(), "note": None} for symbol in symbols]}
        ).encode()
        self._json_request(
            f"v1/lists/{urlquote(list_id, safe='')}/items",
            token,
            data=body,
            method="PUT",
            headers={"If-Match": f'W/"{version}"'},
        )

    def _json_request(
        self,
        path: str,
        token: str,
        data: bytes | None = None,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ticker-cli/0.3",
            **(headers or {}),
        }
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(f"{self.api_url}/", path),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=8) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, dict):
                raise IdentityError("backend returned an invalid response")
            return payload
        except HTTPError as error:
            try:
                payload = json.loads(error.read())
            except (OSError, ValueError, TypeError):
                payload = {}
            finally:
                error.close()
            if (
                error.code == 409
                and isinstance(payload, dict)
                and payload.get("error") == "stale_version"
                and isinstance(payload.get("version"), int)
            ):
                raise WatchlistVersionMismatch(payload["version"]) from error
            raise IdentityError(f"backend returned HTTP {error.code}") from error
        except IdentityError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise IdentityError(f"backend request failed: {error}") from error

    def _register(self) -> str:
        device_name = (platform.node() or "")[:80] or None
        body = json.dumps({"name": device_name}).encode()
        request = Request(
            urljoin(f"{self.api_url}/", "v1/devices"),
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "ticker-cli/0.3"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=8) as response:
                payload = json.loads(response.read())
            token = payload.get("token")
            if not isinstance(token, str) or not token.strip():
                raise IdentityError("device endpoint returned no token")
            return token.strip()
        except IdentityError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise IdentityError(f"device registration failed: {error}") from error

    def _save(self, token: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"token": token}, indent=2) + "\n")
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except OSError as error:
            raise IdentityError(f"could not save device token: {error}") from error


def initialize_device_identity() -> str | None:
    """Return the existing token or register once, preserving local-only startup on failure."""
    try:
        return DeviceIdentity().ensure_token()
    except IdentityError:
        return None


def sync_device_watchlist(symbols: list[str]) -> bool:
    """Upload the final local watchlist without making shutdown depend on the backend."""
    try:
        return DeviceIdentity().sync_watchlist(symbols)
    except IdentityError:
        return False
