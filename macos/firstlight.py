"""FirstLight CR on macOS: one CLI for the Android Emulator workflow.

Replaces the Windows/MuMu PowerShell entry points:

    check_original_game.ps1   -> firstlight check-original
    build_offline_engine.ps1  -> firstlight build
    install_offline_engine.ps1-> firstlight install
    native_runner/start_offline.ps1 -> firstlight start

Run through ``macos/fl`` so the repository virtualenv is used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from native_runner import avd_runtime as avd  # noqa: E402
from native_runner import content_profile  # noqa: E402
from native_runner import posix_build  # noqa: E402
from native_runner.local_config import setting  # noqa: E402

DEFAULT_ORIGINAL = REPO_ROOT / "user-provided" / "nulls-royale.apk"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "cr-ai-offline.apk"


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def run_offline_install(*args: str) -> dict:
    command = [sys.executable, "-m", "native_runner.offline_install", "--adb", str(avd.adb_path()), "--serial", avd.serial(), *args]
    result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"offline_install failed:\n{result.stdout}{result.stderr}")
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    return json.loads(lines[-1]) if lines else {}


def doctor() -> dict:
    checks = {
        "host": f"{platform.system()} {platform.machine()}",
        "content_profile": content_profile.name(),
        "python": sys.version.split()[0],
        "sdk": str(avd.sdk_root()),
        "adb": avd.adb_path().is_file(),
        "emulator": avd.emulator_path().is_file(),
        "system_image": (avd.sdk_root() / "system-images/android-31/google_apis/arm64-v8a").is_dir(),
        "avd": avd.avd_directory().exists(),
        "java": bool(setting("CR_JAVA")) and Path(setting("CR_JAVA")).is_file(),
        "keytool": bool(setting("CR_KEYTOOL")) and Path(setting("CR_KEYTOOL")).is_file(),
        "build_tools": bool(setting("CR_BUILD_TOOLS")) and (Path(setting("CR_BUILD_TOOLS")) / "apksigner").is_file(),
        "ndk": bool(setting("CR_NDK_ROOT")) and Path(setting("CR_NDK_ROOT")).is_dir(),
        "original_apk": Path(setting("CR_INPUT_APK", str(DEFAULT_ORIGINAL))).is_file(),
        "offline_apk": Path(setting("CR_OUTPUT_APK", str(DEFAULT_OUTPUT))).is_file(),
        "device_state": avd.device_state(),
    }
    try:
        import torch

        checks["torch"] = torch.__version__
    except ImportError:
        checks["torch"] = None
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firstlight", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="show which tools and inputs are configured")
    p = sub.add_parser("avd-create", help="create the dedicated Android 12 arm64 AVD")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("avd-start", help="boot the AVD (windowed unless --headless)")
    p.add_argument("--headless", action="store_true")
    sub.add_parser("avd-stop", help="shut the AVD down")
    p = sub.add_parser("install-original", help="install the unmodified original APK (step 1, needs network)")
    p.add_argument("--apk", type=Path, default=Path(setting("CR_INPUT_APK", str(DEFAULT_ORIGINAL))))
    sub.add_parser("open-network", help="lift the offline firewall so the stock game can download its update")
    p = sub.add_parser(
        "seed-base-content", help="base-15.535.3 profile: copy the APK's bundled content into update/ (offline, replaces update-original)"
    )
    p.add_argument("--apk", type=Path, default=Path(setting("CR_INPUT_APK", str(DEFAULT_ORIGINAL))))
    p = sub.add_parser("update-original", help="run the stock game online until its update matches (step 2)")
    p.add_argument("--timeout", type=float, default=3600.0)
    p = sub.add_parser("screenshot", help="save runs/avd/<name>.png from the emulator")
    p.add_argument("--name", default="screen")
    sub.add_parser("prepare", help="root + offline firewall + port forward (start_offline -PrepareOnly)")
    p = sub.add_parser("check-original", help="verify the original APK and downloaded resources")
    p.add_argument("--apk", type=Path, default=Path(setting("CR_INPUT_APK", str(DEFAULT_ORIGINAL))))
    p = sub.add_parser("build", help="build the offline probe APK")
    p.add_argument("--apk", type=Path, default=Path(setting("CR_INPUT_APK", str(DEFAULT_ORIGINAL))))
    p.add_argument("--output", type=Path, default=Path(setting("CR_OUTPUT_APK", str(DEFAULT_OUTPUT))))
    p.add_argument("--engine-count", type=int, default=int(setting("CR_BUILD_ENGINE_COUNT", "1")))
    sub.add_parser("probe", help="only compile libcrprobe.so (toolchain check, no APK needed)")
    p = sub.add_parser("install", help="install the offline APK, then start the engine")
    p.add_argument("--apk", type=Path, default=Path(setting("CR_OUTPUT_APK", str(DEFAULT_OUTPUT))))
    p.add_argument("--restore", help="restore a device-local backup instead of installing")
    sub.add_parser("start", help="restart the offline engine and wait for the control port")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        emit(doctor())
    elif args.command == "avd-create":
        emit({"ok": True, "avd": str(avd.create_avd(force=args.force))})
    elif args.command == "avd-start":
        emit(avd.start_avd(headless=args.headless))
    elif args.command == "avd-stop":
        emit(avd.stop_avd())
    elif args.command == "install-original":
        emit(avd.install_original(args.apk.resolve()))
    elif args.command == "open-network":
        avd.start_avd()
        emit(avd.open_network())
    elif args.command == "seed-base-content":
        avd.prepare()
        emit(run_offline_install("--seed-base-content", str(args.apk.resolve())))
    elif args.command == "update-original":
        if content_profile.is_base():
            raise SystemExit("update-original is for the update-15.535.86 profile; with base-15.535.3 run seed-base-content")
        result = avd.update_original(timeout=args.timeout)
        emit(result)
        return 0 if result["ok"] else 1
    elif args.command == "screenshot":
        emit({"ok": True, "path": str(avd.screenshot(args.name))})
    elif args.command == "prepare":
        emit(avd.prepare())
    elif args.command == "check-original":
        avd.prepare()
        emit(run_offline_install("--check-original", str(args.apk.resolve())))
    elif args.command == "build":
        posix_build.build_offline_engine(args.apk, args.output, args.engine_count)
    elif args.command == "probe":
        posix_build.build_probe(Path(setting("CR_NDK_ROOT")), Path(setting("CR_PROBE", str(posix_build.DEFAULT_PROBE))))
    elif args.command == "install":
        avd.prepare()
        if args.restore:
            emit(run_offline_install("--restore", args.restore))
            return 0
        installed = run_offline_install("--apk", str(args.apk.resolve()))
        try:
            started = avd.start_offline()
        except Exception as error:  # restore exactly like install_offline_engine.ps1
            run_offline_install("--restore", installed["backup"], "--receipt", installed["receipt"])
            raise RuntimeError(f"Offline startup failed; previous APK and app data restored. {error}") from error
        emit({**started, "backup": installed["backup"], "resource_files_verified": installed["resource_files_verified"]})
    elif args.command == "start":
        emit(avd.start_offline())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
