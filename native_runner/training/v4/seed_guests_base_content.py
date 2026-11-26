"""Seed the APK-bundled base content into freshly booted PPO guests (base-15.535.3 profile).

The engine node normally relies on CR_EMULATOR_INITDATA already holding the
matching update/ cache. Recent Android Emulator builds (37.x) keep a per-instance
encryption key, so a cloned userdata image cannot be decrypted, Android formats
/data and every guest boots fresh: the node reinstalls the APK, but update/ is
empty and the engine never attests production_ready. This helper seeds each
guest the same way `offline_install --seed-base-content` does, after root and
the offline firewall are in place, so guests no longer depend on the cloned data.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from native_runner import content_profile, offline_install  # noqa: E402
from native_runner.local_config import setting  # noqa: E402
from native_runner.training.v4 import configure_emulator_engine_guests as guests  # noqa: E402


def seed(serial: str, adb: str, apk: Path) -> dict:
    guests.wait_root(serial)
    guests.configure_firewall(serial)
    device = offline_install.Device(adb, serial)
    device.require_offline()
    result = offline_install.seed_base_content(device, apk)
    return {"serial": serial, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serials", required=True, help="comma-separated emulator serials")
    parser.add_argument("--apk", type=Path, default=Path(setting("CR_INPUT_APK", "user-provided/nulls-royale.apk")))
    parser.add_argument("--adb", default=setting("CR_TRAINING_ADB", "adb"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not content_profile.is_base():
        print(json.dumps({"ok": True, "skipped": f"CR_CONTENT_PROFILE is {content_profile.name()}"}))
        return 0
    guests.ADB = Path(args.adb)
    serials = [item for item in args.serials.split(",") if item]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(serials)) as pool:
        results = list(pool.map(lambda serial: seed(serial, args.adb, args.apk.resolve()), serials))
    report = {"ok": True, "content_profile": content_profile.name(), "guests": results}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
