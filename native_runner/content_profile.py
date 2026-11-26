"""Selectable game-content profile for the offline engine.

``update-15.535.86`` (default) keeps the original FirstLight pins: the device
must hold the 15.535.86 content update downloaded by the stock client.

``base-15.535.3`` uses the content bundled inside the 15.535.13 APK. Null's
servers moved to V16 in November 2026 and no longer deliver 15.535.86, so the
original route cannot be reproduced on a fresh device. This profile seeds the
app's ``update/`` directory from the APK's own ``assets/fingerprint.json`` and
``assets/assets.scdb`` and pins their hashes; live attestation stays
fail-closed against those pins. Location 15000199 exists only in 15.535.86, so
matches default to a base-content PvP arena.

Select it with ``CR_CONTENT_PROFILE=base-15.535.3`` in ``.env``.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import zipfile

from .local_config import setting

PACKAGE_ROOT = Path(__file__).resolve().parent
UPDATE_PROFILE = "update-15.535.86"
BASE_PROFILE = "base-15.535.3"
PROFILES_ROOT = PACKAGE_ROOT / "data" / "content_profiles"
UPDATE_DEFAULT_LOCATION = 15000199


def name() -> str:
    value = setting("CR_CONTENT_PROFILE", UPDATE_PROFILE)
    if value not in {UPDATE_PROFILE, BASE_PROFILE}:
        raise ValueError(f"Unknown CR_CONTENT_PROFILE {value!r}; use {UPDATE_PROFILE!r} or {BASE_PROFILE!r}")
    return value


def is_base() -> bool:
    return name() == BASE_PROFILE


@lru_cache(maxsize=None)
def base_profile() -> dict:
    return json.loads((PROFILES_ROOT / f"{BASE_PROFILE}.json").read_text(encoding="utf-8"))


def default_location() -> int:
    return int(base_profile()["default_location"]) if is_base() else UPDATE_DEFAULT_LOCATION


def runtime_fingerprint_sha256() -> str:
    if is_base():
        return base_profile()["fingerprint_sha256"]
    from .offline_build import supported_engine

    return supported_engine()["runtime_fingerprint_sha256"]


def pinned_runtime_files() -> dict[str, str]:
    """Device ``update/`` files the base profile pins, by file name."""
    profile = base_profile()
    return {"fingerprint.json": profile["fingerprint_sha256"], "assets.scdb": profile["assets_sha256"]}


def ruleset_file_pins() -> dict[str, str]:
    """Ruleset ``data``/``visual`` entries that replace the 15.535.86 update files (empty by default)."""
    if not is_base():
        return {}
    return {f"{BASE_PROFILE}/{file}": digest for file, digest in pinned_runtime_files().items()}


def seed_payloads(apk: str | Path) -> dict[str, bytes]:
    """Read the base-content files from the original APK and check them against the pins."""
    profile = base_profile()
    pins = pinned_runtime_files()
    payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(apk) as archive:
        for target, entry in profile["seed_files"].items():
            data = archive.read(entry)
            if hashlib.sha256(data).hexdigest() != pins[target]:
                raise ValueError(f"APK {entry} differs from the {BASE_PROFILE} pin")
            payloads[target] = data
    return payloads
