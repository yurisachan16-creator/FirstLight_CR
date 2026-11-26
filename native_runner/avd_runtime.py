"""Android Emulator (AVD) lifecycle for macOS/Linux hosts.

Port of ``start_offline.ps1`` for a dedicated, rooted Google APIs AVD instead of
MuMu. Apple-silicon Macs run the arm64-v8a system image natively. The steps and
readiness checks are the same as the Windows script: root adbd, dual-stack
offline firewall, screen stay-on, control-port forward, a fresh game process
and a cold-ready native control port.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from .local_config import setting
from .training.v4 import configure_emulator_engine_guests as guests

PACKAGE = "nullsroyale.rel.free"
ACTIVITY = "com.supercell.clashroyale.GameApp"
SYSTEM_IMAGE = "system-images;android-31;google_apis;arm64-v8a"
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS = REPO_ROOT / "runs" / "avd"


def sdk_root() -> Path:
    return Path(setting("CR_ANDROID_SDK", str(Path.home() / "Library" / "Android" / "sdk"))).expanduser()


def adb_path() -> Path:
    return Path(setting("CR_ADB", str(sdk_root() / "platform-tools" / "adb"))).expanduser()


def emulator_path() -> Path:
    return Path(setting("CR_EMULATOR", str(sdk_root() / "emulator" / "emulator"))).expanduser()


def avd_name() -> str:
    return setting("CR_AVD_NAME", "firstlight-a12")


def serial() -> str:
    value = setting("CR_ADB_SERIAL", "emulator-5554")
    if not re.fullmatch(r"emulator-(\d+)", value):
        raise ValueError(f"CR_ADB_SERIAL must be an emulator serial such as emulator-5554, got {value!r}")
    return value


def console_port() -> int:
    return int(serial().split("-", 1)[1])


def control_ports() -> tuple[int, int]:
    host = int(setting("CR_CONTROL_PORT", "26789"))
    guest = int(setting("CR_GUEST_CONTROL_PORT", "26789"))
    for port in (host, guest):
        if not 1 <= port <= 65535:
            raise ValueError("Control ports must be in 1..65535.")
    return host, guest


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["ANDROID_SDK_ROOT"] = str(sdk_root())
    env["ANDROID_HOME"] = str(sdk_root())
    java = setting("CR_JAVA")
    if java:
        env.setdefault("JAVA_HOME", str(Path(java).resolve().parents[1]))
    return env


def adb(*args: str, check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(adb_path()), "-s", serial(), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {(result.stdout + result.stderr).strip()}")
    return result


def device_state() -> str:
    result = subprocess.run([str(adb_path()), "devices"], capture_output=True, text=True, timeout=20, check=False)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == serial():
            return parts[1]
    return "absent"


# ---------------------------------------------------------------- AVD lifecycle


def avd_directory() -> Path:
    home = Path(setting("ANDROID_AVD_HOME", str(Path.home() / ".android" / "avd"))).expanduser()
    return home / f"{avd_name()}.avd"


def create_avd(force: bool = False) -> Path:
    avdmanager = sdk_root() / "cmdline-tools" / "latest" / "bin" / "avdmanager"
    if not avdmanager.is_file():
        raise FileNotFoundError(f"Missing avdmanager: {avdmanager}")
    if avd_directory().exists() and not force:
        raise FileExistsError(f"AVD already exists: {avd_directory()} (pass --force to recreate)")
    # Apple silicon runs the arm64 image natively; x86_64 Linux hosts use the x86_64 image,
    # whose Android 11+ builds translate the game's arm64 libraries (as the Linux AVD guide does).
    image = setting("CR_AVD_SYSTEM_IMAGE", SYSTEM_IMAGE)
    command = [str(avdmanager), "create", "avd", "-n", avd_name(), "-k", image, "-d", "pixel_4"]
    if force:
        command.append("--force")
    subprocess.run(command, input="no\n", text=True, env=_env(), check=True, timeout=300)
    config = avd_directory() / "config.ini"
    overrides = {
        "hw.ramSize": setting("CR_AVD_MEMORY_MB", "3072"),
        "hw.cpu.ncore": setting("CR_AVD_CORES", "4"),
        "disk.dataPartition.size": setting("CR_AVD_DATA_SIZE", "12G"),
        # The probe's native touch interceptor maps hand/arena taps with a fixed
        # 1080x1920 layout (MuMu's default), so the AVD must use the same pixels.
        "hw.lcd.width": "1080",
        "hw.lcd.height": "1920",
        "hw.lcd.density": "440",
        "hw.keyboard": "yes",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": setting("CR_EMULATOR_GPU", "host"),
        "showDeviceFrame": "no",
        "hw.audioInput": "no",
        "hw.audioOutput": "no",
        "fastboot.forceColdBoot": "yes",
    }
    # Keep userdata in the AVD directory so the installed game and its update survive reboots.
    dropped = set(overrides) | {"disk.dataPartition.path"}
    lines = [line for line in config.read_text(encoding="utf-8").splitlines() if line.split("=", 1)[0].strip() not in dropped]
    lines += [f"{key}={value}" for key, value in overrides.items()]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return avd_directory()


def wait_boot(timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device_state() == "device":
            booted = adb("shell", "getprop", "sys.boot_completed", check=False, timeout=15).stdout.strip()
            if booted == "1":
                return
        time.sleep(2.0)
    raise TimeoutError(f"{serial()} did not finish Android startup within {timeout:.0f} seconds")


def start_avd(headless: bool = False, wipe: bool = False) -> dict:
    """Boot the dedicated AVD if it is not already running."""
    if device_state() != "device":
        if not avd_directory().exists():
            raise FileNotFoundError(f"AVD {avd_name()} does not exist; run `firstlight avd-create` first")
        RUNS.mkdir(parents=True, exist_ok=True)
        gpu = setting("CR_EMULATOR_GPU", "host")
        command = [
            str(emulator_path()),
            "-avd", avd_name(),
            "-port", str(console_port()),
            "-memory", setting("CR_AVD_MEMORY_MB", "3072"),
            "-cores", setting("CR_AVD_CORES", "4"),
            "-gpu", "swiftshader_indirect" if headless and gpu == "host" else gpu,
            "-no-snapshot",
            "-no-boot-anim",
            "-no-audio",
            "-netdelay", "none",
            "-netspeed", "full",
        ]
        if headless:
            command.append("-no-window")
        if wipe:
            command.append("-wipe-data")
        log = (RUNS / "emulator.log").open("ab")
        subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_env(), start_new_session=True)
    wait_boot()
    return {"ok": True, "serial": serial(), "avd": avd_name(), "release": adb("shell", "getprop", "ro.build.version.release").stdout.strip()}


def stop_avd() -> dict:
    """Flush and power the guest off cleanly before killing the emulator.

    ``emu kill`` alone is a power cut: files written shortly before (for example
    the seeded update/ content restored by offline_install) can be lost, and a
    userdata image exported afterwards then boots without them.
    """
    if device_state() == "absent":
        return {"ok": True, "stopped": False}
    adb("shell", "sync", check=False, timeout=60)
    adb("shell", "reboot", "-p", check=False, timeout=30)
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline and device_state() != "absent":
        time.sleep(1.0)
    if device_state() != "absent":
        adb("emu", "kill", check=False)
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and device_state() != "absent":
            time.sleep(1.0)
    return {"ok": device_state() == "absent", "stopped": True}


def install_original(apk: Path) -> dict:
    """Install the unmodified original APK so the game can download its update online."""
    start_avd()
    result = adb("install", "-r", str(apk), check=False, timeout=600)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Success" not in output:
        raise RuntimeError(f"Original APK install failed: {output}")
    return {"ok": True, "installed": str(apk)}


def open_network() -> dict:
    """Remove the offline firewall jump so the stock game can update. Never use with the probe APK."""
    removed = {}
    for family in ("iptables", "ip6tables"):
        result = adb("shell", family, "-D", "OUTPUT", "-j", "CR_NATIVE_OFFLINE", check=False)
        removed[family] = result.returncode == 0
    return {"ok": True, "removed_offline_jump": removed}


def screenshot(name: str) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{name}.png"
    result = subprocess.run(
        [str(adb_path()), "-s", serial(), "exec-out", "screencap", "-p"], capture_output=True, timeout=30, check=False
    )
    path.write_bytes(result.stdout)
    return path


def update_original(timeout: float = 3600.0, interval: float = 20.0) -> dict:
    """Open the stock game online and wait until its downloaded update matches supported_engine.json.

    Screenshots are written to runs/avd/update-latest.png so progress can be checked remotely;
    login/consent dialogs, if any, still need a tap in the emulator window.
    """
    from .offline_build import supported_engine

    expected = supported_engine()["runtime_fingerprint_sha256"]
    start_avd()
    guests.ADB = adb_path()
    guests.wait_root(serial())
    open_network()
    fingerprint = f"/data/user/0/{PACKAGE}/update/fingerprint.json"
    if not _package_pids():
        adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")
    deadline = time.monotonic() + timeout
    observed = ""
    while time.monotonic() < deadline:
        result = adb("shell", "sha256sum", fingerprint, check=False, timeout=20)
        observed = result.stdout.split()[0] if result.returncode == 0 and result.stdout.strip() else ""
        screenshot("update-latest")
        if observed == expected:
            time.sleep(interval)  # let the client finish writing the remaining files
            adb("shell", "am", "force-stop", PACKAGE)
            return {"ok": True, "fingerprint": observed, "matches_supported_engine": True}
        if not _package_pids():
            adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")
        time.sleep(interval)
    return {"ok": False, "fingerprint": observed or None, "expected": expected, "screenshot": str(RUNS / "update-latest.png")}


# ------------------------------------------------------- start_offline.ps1 port


def _package_pids() -> list[str]:
    result = adb("shell", "pidof", PACKAGE, check=False, timeout=15)
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return []
    pids = sorted(set(raw.split()))
    for pid in pids:
        if not re.fullmatch(r"[1-9][0-9]*", pid):
            raise RuntimeError(f"pidof returned an invalid PID for {PACKAGE}: {raw}")
    return pids


def _wait_package_stopped(previous: list[str]) -> None:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if not _package_pids():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Package {PACKAGE} did not fully stop (old PIDs={','.join(previous) or 'none'}, remaining={','.join(_package_pids())}).")


def _wait_new_pid(previous: list[str]) -> str:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        current = _package_pids()
        if len(current) == 1:
            if current[0] in previous:
                raise RuntimeError(f"Package {PACKAGE} reused an old PID ({current[0]}); refusing an ambiguous restart.")
            return current[0]
        if len(current) > 1:
            raise RuntimeError(f"Package {PACKAGE} started with ambiguous PIDs: {','.join(current)}.")
        time.sleep(0.1)
    raise RuntimeError(f"Package {PACKAGE} did not publish a new PID within 30 seconds.")


def _status(port: int) -> dict | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as sock:
            sock.settimeout(2.0)
            sock.sendall(b"status\n")
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        line = data.split(b"\n", 1)[0].strip()
        return json.loads(line) if line else None
    except (OSError, ValueError):
        return None


def _null_pointer(value) -> bool:
    return value is not None and str(value).strip().lower() in {"0", "0x0", "(nil)"}


def _wait_control_ready(port: int, expected_pid: str) -> dict:
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if _package_pids() != [expected_pid]:
            raise RuntimeError(f"Package PID changed while waiting for control readiness (expected={expected_pid}, actual={','.join(_package_pids()) or 'none'}).")
        status = _status(port)
        if (
            status is not None
            and {"generation", "manager", "configured"} <= status.keys()
            and status.get("ok")
            and status.get("coldReady")
            and not status.get("configured")
            and int(status["generation"]) == 0
            and _null_pointer(status["manager"])
        ):
            return status
        time.sleep(0.25)
    raise RuntimeError("Native control port did not become cold-ready within 45 seconds.")


def prepare(require_android_12: bool = True) -> dict:
    """Boot, root, isolate and forward the control port (``start_offline.ps1 -PrepareOnly``)."""
    start_avd(headless=setting("CR_EMULATOR_HEADLESS", "false").lower() == "true")
    guests.ADB = adb_path()
    identity = guests.wait_root(serial())
    if "uid=0(root)" not in identity:
        raise RuntimeError("The isolated harness adbd is not running as root.")
    if require_android_12:
        release = adb("shell", "getprop", "ro.build.version.release").stdout.strip()
        if not re.match(r"^12(?:\.|$)", release):
            raise RuntimeError(f"The AVD must run Android 12; reported: {release}")
    rules = guests.configure_firewall(serial())
    # Native arm64 tags heap pointers; the probe expects untagged ones (as under MuMu's translation).
    # The rebuilt APK opts out in its manifest; this also covers APKs built before that change.
    adb("shell", "am", "compat", "disable", "NATIVE_HEAP_POINTER_TAGGING", PACKAGE, check=False)
    adb("shell", "svc", "power", "stayon", "true")
    # Suppress Android's one-time "Viewing full screen" banner over the battle.
    adb("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed", check=False)
    host_port, guest_port = control_ports()
    adb("forward", f"tcp:{host_port}", f"tcp:{guest_port}")
    return {
        "ok": True,
        "serial": serial(),
        "avd": avd_name(),
        "offlineIPv4": bool(rules.get("iptables")),
        "offlineIPv6": bool(rules.get("ip6tables")),
        "preparedOnly": True,
        "controlPort": host_port,
    }


def start_offline() -> dict:
    """Full ``start_offline.ps1``: restart the game and wait for a cold-ready control port."""
    prepared = prepare()
    old_pids = _package_pids()
    adb("shell", "am", "force-stop", PACKAGE)
    _wait_package_stopped(old_pids)
    adb("shell", "am", "start", "-n", f"{PACKAGE}/{ACTIVITY}")
    new_pid = _wait_new_pid(old_pids)
    status = _wait_control_ready(prepared["controlPort"], new_pid)
    return {
        "ok": True,
        "instance": avd_name(),
        "serial": serial(),
        "offlineIPv4": prepared["offlineIPv4"],
        "offlineIPv6": prepared["offlineIPv6"],
        "coldReady": bool(status.get("coldReady")),
        "replayBootstrap": bool(status.get("replayBootstrap")),
        "fixtureCommands": status.get("fixtureCommands"),
        "controlPort": prepared["controlPort"],
        "processId": int(new_pid),
    }
