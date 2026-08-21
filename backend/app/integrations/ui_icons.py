"""Official Ubiquiti product icons, fetched once and cached on disk.

Ubiquiti publishes a device fingerprint catalogue listing every SKU with the
id of its product icon. The Integration API only ever gives us a model string
("U7-Pro-Wall", "Express 7"), so this module maps that string to an icon and
keeps the PNG locally: the browser then loads icons from this backend, which
works on a LAN-only machine and keeps the repository free of third-party
binaries.

Nothing here is load-bearing. Every failure path returns None and the UI falls
back to a drawn glyph, so an offline console still renders a full dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CATALOGUE_URL = "https://static.ui.com/fingerprint/ui/public.json"
ICON_URL = "https://static.ui.com/fingerprint/ui/icons/{icon_id}_{size}x{size}.png"
# One of the resolutions the catalogue publishes; ~8 KB per device, which is
# small enough that a retina tile can downscale from it.
ICON_SIZE = 257
CATALOGUE_TTL_SEC = 24 * 3600
TIMEOUT_SEC = 10.0
# Icon ids are UUIDs. Checking that before building a URL means a surprise in
# the upstream catalogue cannot steer a request somewhere else.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Long enough for any real model string; anything longer is not worth a lookup.
MAX_MODEL_LEN = 64


def normalize(model: str) -> str:
    """Fold the spelling differences between the two sources.

    The API reports "Express 7" and "U7-Pro-Wall"; the catalogue lists "UX7"
    with a product name of "Express 7", and shortnames that vary in hyphens.
    Comparing on alphanumerics alone makes those the same key.
    """
    return re.sub(r"[^a-z0-9]", "", model.lower())


class IconCatalogue:
    """Model -> icon id index, cached in memory and on disk."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._index: dict[str, str] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def _catalogue_path(self) -> Path:
        return self._dir / "catalogue.json"

    def _icon_path(self, icon_id: str) -> Path:
        # Keyed by icon id, never by the caller's model string: a UUID cannot
        # escape the cache directory.
        return self._dir / f"{icon_id}_{ICON_SIZE}.png"

    @staticmethod
    def build_index(catalogue: dict) -> dict[str, str]:
        index: dict[str, str] = {}
        for device in catalogue.get("devices", []):
            icon_id = (device.get("icon") or {}).get("id")
            if not icon_id or not _UUID_RE.match(str(icon_id)):
                continue
            names = [
                device.get("sku"),
                (device.get("product") or {}).get("name"),
                (device.get("compliance") or {}).get("modelName"),
                *(device.get("shortnames") or []),
            ]
            for name in names:
                if not name:
                    continue
                # First writer wins: entries are ordered so that a SKU's own
                # device claims the key before a later device's shortname can.
                index.setdefault(normalize(str(name)), icon_id)
        return index

    def _load_from_disk(self) -> bool:
        path = self._catalogue_path
        try:
            age = time.time() - path.stat().st_mtime
            if age > CATALOGUE_TTL_SEC:
                return False
            self._index = self.build_index(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            logger.debug("Icon catalogue cache unusable: %s", exc)
            return False
        self._fetched_at = time.time()
        return bool(self._index)

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as http:
            resp = await http.get(CATALOGUE_URL)
            resp.raise_for_status()
            catalogue = resp.json()
        self._index = self.build_index(catalogue)
        self._fetched_at = time.time()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._catalogue_path.write_text(json.dumps(catalogue))
        except OSError as exc:
            # A read-only cache directory costs a refetch per restart, not
            # correctness.
            logger.info("Could not cache the icon catalogue: %s", exc)

    def _fresh(self) -> bool:
        return bool(self._index) and time.time() - self._fetched_at < CATALOGUE_TTL_SEC

    async def icon_id(self, model: str) -> str | None:
        if not self._fresh():
            async with self._lock:
                # Another request may have refreshed it while we waited.
                if not self._fresh() and not self._load_from_disk():
                    await self._refresh()
        return self._index.get(normalize(model))

    async def png(self, model: str) -> bytes | None:
        """PNG bytes for a model, or None if it has no icon we can fetch."""
        if not model or len(model) > MAX_MODEL_LEN:
            return None
        try:
            icon_id = await self.icon_id(model)
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Icon catalogue fetch failed: %s", exc)
            return None
        if not icon_id:
            return None

        path = self._icon_path(icon_id)
        try:
            return path.read_bytes()
        except OSError:
            pass

        url = ICON_URL.format(icon_id=icon_id, size=ICON_SIZE)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as http:
                resp = await http.get(url)
                resp.raise_for_status()
                data = resp.content
        except httpx.HTTPError as exc:
            logger.info("Icon fetch failed for %s: %s", model, exc)
            return None

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            logger.info("Could not cache icon for %s: %s", model, exc)
        return data
