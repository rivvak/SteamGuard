"""Roblox HTTP API helpers for the local asset copier."""
from __future__ import annotations

import random
import re
import string
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

ASSET_DELIVERY_URL = "https://assetdelivery.roblox.com/v1/asset/"
PUBLISH_URL = "https://www.roblox.com/ide/publish/uploadnewanimation"
CSRF_PROBE_URL = "https://auth.roblox.com/v2/logout"
ROBLOX_STUDIO_UA = "RobloxStudio/WinInet"


class RobloxApiError(RuntimeError):
    """Raised when a Roblox request fails."""


@dataclass(frozen=True)
class PublishResult:
    old_id: str
    new_id: str
    raw_response: str


def normalize_cookie(cookie: str) -> str:
    """Return a bare .ROBLOSECURITY value from either raw value or cookie header text."""
    cookie = (cookie or "").strip()
    if not cookie:
        return ""
    match = re.search(r"\.ROBLOSECURITY\s*=\s*([^;]+)", cookie, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return cookie.strip().strip(';')


def random_asset_text(min_len: int = 4, max_len: int = 6) -> str:
    """Generate the short random names/descriptions used by the original copier."""
    alphabet = string.ascii_lowercase
    return "".join(random.choice(alphabet) for _ in range(random.randint(min_len, max_len)))


class RobloxApiClient:
    def __init__(self, cookie: str, log: Optional[Callable[[str], None]] = None, timeout: int = 60):
        self.cookie = normalize_cookie(cookie)
        if not self.cookie:
            raise RobloxApiError("A .ROBLOSECURITY cookie is required.")
        self.timeout = timeout
        self.log = log or (lambda _msg: None)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": ROBLOX_STUDIO_UA})
        self.session.cookies.set(".ROBLOSECURITY", self.cookie, domain=".roblox.com")
        self._csrf_token: Optional[str] = None

    @property
    def cookie_header(self) -> str:
        return f".ROBLOSECURITY={self.cookie};"

    def get_csrf_token(self, force: bool = False) -> str:
        """Fetch a Roblox CSRF token via the normal 403 response-header pattern."""
        if self._csrf_token and not force:
            return self._csrf_token
        self.log("Requesting Roblox CSRF token...")
        response = self.session.post(
            CSRF_PROBE_URL,
            headers={"Cookie": self.cookie_header, "User-Agent": ROBLOX_STUDIO_UA},
            timeout=self.timeout,
        )
        token = response.headers.get("x-csrf-token") or response.headers.get("X-CSRF-Token")
        if not token:
            raise RobloxApiError(f"Could not obtain CSRF token (HTTP {response.status_code}).")
        self._csrf_token = token
        return token

    def download_asset(self, asset_id: str) -> bytes:
        asset_id = str(asset_id).strip()
        if not asset_id:
            raise RobloxApiError("Missing asset id.")
        self.log(f"Downloading animation {asset_id}...")
        response = self.session.get(
            ASSET_DELIVERY_URL,
            params={"id": asset_id},
            headers={"Cookie": self.cookie_header, "User-Agent": ROBLOX_STUDIO_UA},
            timeout=self.timeout,
        )
        if not response.ok:
            raise RobloxApiError(f"Download failed for {asset_id}: HTTP {response.status_code} - {response.text[:300]}")
        if not response.content:
            raise RobloxApiError(f"Download returned empty content for {asset_id}.")
        return response.content

    def _publish_once(self, data: bytes, name: str, description: str, group_id: Optional[int] = None) -> str:
        params = {
            "assetTypeName": "Animation",
            "name": name,
            "description": description,
            "AllID": "1",
            "ispublic": "False",
            "allowComments": "True",
            "isGamesAsset": "False",
        }
        if group_id is not None:
            params["groupId"] = str(group_id)
        url = f"{PUBLISH_URL}?{urlencode(params)}"
        headers = {
            "Cookie": self.cookie_header,
            "X-CSRF-Token": self.get_csrf_token(),
            "User-Agent": ROBLOX_STUDIO_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        }
        response = self.session.post(url, data=data, headers=headers, timeout=self.timeout)
        if response.status_code == 403 and response.headers.get("x-csrf-token"):
            self._csrf_token = response.headers["x-csrf-token"]
            headers["X-CSRF-Token"] = self._csrf_token
            response = self.session.post(url, data=data, headers=headers, timeout=self.timeout)
        if not response.ok:
            raise RobloxApiError(f"Publish failed: HTTP {response.status_code} - {response.text[:500]}")
        text = response.text.strip()
        match = re.search(r"\d+", text)
        if not match:
            raise RobloxApiError(f"Publish response did not include a new asset id: {text[:300]}")
        return match.group(0)

    def publish_animation(self, old_id: str, data: bytes, group_id: Optional[int] = None, retries: int = 4) -> PublishResult:
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            name = random_asset_text()
            description = random_asset_text()
            try:
                self.log(f"Publishing animation {old_id} (attempt {attempt}/{retries}) as {name}...")
                new_id = self._publish_once(data, name, description, group_id)
                self.log(f"Published {old_id} -> {new_id}")
                return PublishResult(old_id=str(old_id), new_id=str(new_id), raw_response=str(new_id))
            except Exception as exc:  # requests exceptions included
                last_error = exc
                self.log(f"Retrying {old_id}: {exc}")
                if attempt < retries:
                    time.sleep(1.25 * attempt)
        raise RobloxApiError(f"Failed to publish {old_id} after {retries} attempts: {last_error}")

    def copy_animation(self, old_id: str, group_id: Optional[int] = None, retries: int = 4) -> PublishResult:
        data = self.download_asset(old_id)
        return self.publish_animation(old_id, data, group_id=group_id, retries=retries)
