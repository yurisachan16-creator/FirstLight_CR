"""POSIX (macOS/Linux) port of the Windows build scripts.

Mirrors ``probe/build_probe.ps1``, ``build_engine_cluster_apk.ps1`` and
``build_offline_engine.ps1`` step for step so that the offline APK can be built
on a Mac or Linux host. Nothing here launches the game or downloads game
content; the only download is the pinned Apktool jar, verified by SHA-256.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import urllib.request
import uuid

from . import offline_build
from .local_config import setting

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PROBE_DIR = PACKAGE_ROOT / "probe"
PROBE_OUT = PROBE_DIR / "out"
DEFAULT_PROBE = PROBE_OUT / "libcrprobe.so"

PACKAGE = "nullsroyale.rel.free"
APKTOOL_VERSION = "2.12.1"
APKTOOL_SHA256 = "66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa"
APKTOOL_URL = (
    f"https://github.com/iBotPeaches/Apktool/releases/download/v{APKTOOL_VERSION}/apktool_{APKTOOL_VERSION}.jar"
)
APKTOOL = PROBE_OUT / "tools" / f"apktool_{APKTOOL_VERSION}.jar"
KEYSTORE = PROBE_DIR / "crprobe-cluster.keystore"
KEYSTORE_PASSWORD = Path(f"{KEYSTORE}.pass")
KEY_ALIAS = "crprobe-cluster"

APPLICATION_ANCHOR = '<application android:allowBackup="true"'
MANIFEST_ANCHOR = '        <meta-data android:name="io.sentry.auto-init"'

APPLICATION_SMALI = """.class public Lcom/supercell/clashroyale/EngineClusterApplication;
.super Landroid/app/Application;

.method public constructor <init>()V
    .locals 0
    invoke-direct {p0}, Landroid/app/Application;-><init>()V
    return-void
.end method

.method protected attachBaseContext(Landroid/content/Context;)V
    .locals 4
    invoke-super {p0, p1}, Landroid/app/Application;->attachBaseContext(Landroid/content/Context;)V
    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I
    const/16 v1, 0x1c
    if-lt v0, v1, :done
    invoke-static {}, Landroid/app/Application;->getProcessName()Ljava/lang/String;
    move-result-object v0
    const-string v1, ":engine"
    invoke-virtual {v0, v1}, Ljava/lang/String;->lastIndexOf(Ljava/lang/String;)I
    move-result v2
    if-ltz v2, :done
    add-int/lit8 v2, v2, 0x1
    invoke-virtual {v0, v2}, Ljava/lang/String;->substring(I)Ljava/lang/String;
    move-result-object v3
    const-string v0, "engine0"
    invoke-virtual {v3, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z
    move-result v0
    if-nez v0, :done
    invoke-static {v3}, Landroid/webkit/WebView;->setDataDirectorySuffix(Ljava/lang/String;)V
    :done
    return-void
.end method
"""

ENGINE_SMALI = """.class public Lcom/supercell/clashroyale/{name};
.super Lcom/supercell/clashroyale/GameApp;

.method public constructor <init>()V
    .locals 0
    invoke-direct {{p0}}, Lcom/supercell/clashroyale/GameApp;-><init>()V
    return-void
.end method
"""


def _run(*command: str | Path, capture: bool = False) -> str:
    result = subprocess.run(
        [str(part) for part in command],
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if result.returncode != 0:
        detail = f"\n{result.stdout}" if capture and result.stdout else ""
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(map(str, command))}{detail}")
    return result.stdout or ""


def ndk_host_tag() -> str:
    system = platform.system()
    if system == "Darwin":
        # NDK r23+ ships universal darwin-x86_64 toolchains that run natively on Apple silicon.
        return "darwin-x86_64"
    if system == "Linux":
        return "linux-x86_64"
    raise RuntimeError(f"Unsupported build host: {system}; use build_probe.ps1 on Windows")


def build_probe(ndk_root: Path, output: Path = DEFAULT_PROBE) -> Path:
    """Port of probe/build_probe.ps1."""
    compiler = ndk_root / "toolchains" / "llvm" / "prebuilt" / ndk_host_tag() / "bin" / "aarch64-linux-android24-clang++"
    if not compiler.is_file():
        raise FileNotFoundError(f"Missing NDK compiler: {compiler}")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        compiler,
        "-std=c++17",
        "-O2",
        "-fPIC",
        "-fvisibility=hidden",
        "-Wall",
        "-Wextra",
        "-shared",
        "-Wl,-z,max-page-size=16384",
        "-o",
        output,
        PROBE_DIR / "cr_replay_probe.cpp",
        PROBE_DIR / "native_touch_interceptor.cpp",
        PROBE_DIR / "native_call_arm64.S",
        PROBE_DIR / "remaining_runtime_arm64.S",
        "-llog",
        "-ldl",
        "-lz",
    )
    print(f"Built probe: {output} ({output.stat().st_size} bytes)", flush=True)
    return output


def ensure_apktool(java: Path) -> Path:
    APKTOOL.parent.mkdir(parents=True, exist_ok=True)
    if not APKTOOL.is_file():
        local = setting("CR_APKTOOL_JAR")
        if local and Path(local).is_file():
            shutil.copyfile(local, APKTOOL)
        else:
            with urllib.request.urlopen(APKTOOL_URL, timeout=120) as response, APKTOOL.open("wb") as target:
                shutil.copyfileobj(response, target)
    actual = offline_build.sha256(APKTOOL)
    if actual != APKTOOL_SHA256:
        raise RuntimeError(f"Apktool {APKTOOL_VERSION} SHA-256 mismatch: {actual}")
    reported = _run(java, "-jar", APKTOOL, "--version", capture=True).strip().splitlines()[-1].strip()
    if reported != APKTOOL_VERSION:
        raise RuntimeError(f"Expected Apktool {APKTOOL_VERSION}, got '{reported}'.")
    return APKTOOL


def patch_cluster_manifest(manifest_text: str, engine_count: int, debuggable: bool = False) -> str:
    if APPLICATION_ANCHOR not in manifest_text or MANIFEST_ANCHOR not in manifest_text:
        raise RuntimeError("Could not find the application manifest insertion point.")
    application = '<application android:name="com.supercell.clashroyale.EngineClusterApplication"'
    text = manifest_text.replace(APPLICATION_ANCHOR, f'{application} android:allowBackup="true"')
    if debuggable:
        text = text.replace(application, f'{application} android:debuggable="true"')
    activities = []
    for engine_id in range(engine_count):
        activities.append(
            '        <activity android:configChanges="keyboard|keyboardHidden|navigation|orientation|screenLayout|'
            'screenSize|smallestScreenSize" '
            'android:excludeFromRecents="true" android:exported="true" android:launchMode="singleTask" '
            f'android:name="com.supercell.clashroyale.Engine{engine_id}App" android:process=":engine{engine_id}" '
            f'android:screenOrientation="sensorPortrait" android:taskAffinity="{PACKAGE}.engine{engine_id}" '
            'android:theme="@android:style/Theme.NoTitleBar.Fullscreen" '
            'android:windowSoftInputMode="adjustNothing|stateUnchanged"/>'
        )
    return text.replace(MANIFEST_ANCHOR, "\n".join(activities) + "\n" + MANIFEST_ANCHOR)


def _signing_password(keytool: Path) -> str:
    if KEYSTORE.exists() != KEYSTORE_PASSWORD.exists():
        raise RuntimeError("Cluster signing key/password files are incomplete; keep or remove both.")
    if not KEYSTORE.exists():
        password = secrets.token_hex(32)
        KEYSTORE_PASSWORD.write_text(password, encoding="utf-8")
        os.chmod(KEYSTORE_PASSWORD, 0o600)
        _run(
            keytool,
            "-genkeypair",
            "-keystore",
            KEYSTORE,
            "-storepass",
            password,
            "-keypass",
            password,
            "-alias",
            KEY_ALIAS,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "36500",
            "-dname",
            "CN=Firstlight CR Local Build",
            "-noprompt",
            capture=True,
        )
    password = KEYSTORE_PASSWORD.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("The local cluster signing password file is empty.")
    return password


def build_cluster_apk(
    decoded_apk: Path,
    probe: Path,
    output_apk: Path,
    engine_count: int,
    java: Path,
    keytool: Path,
    build_tools: Path,
    debuggable: bool = False,
) -> dict:
    """Port of build_engine_cluster_apk.ps1."""
    if not 1 <= engine_count <= 24:
        raise ValueError("The cluster APK supports between 1 and 24 engine processes.")
    zipalign = build_tools / "zipalign"
    apksigner = build_tools / "apksigner"
    for required in (
        decoded_apk,
        decoded_apk / "AndroidManifest.xml",
        decoded_apk / "apktool.yml",
        decoded_apk / "smali" / "com" / "supercell" / "clashroyale" / "GameApp.smali",
        probe,
        java,
        keytool,
        zipalign,
        apksigner,
    ):
        if not Path(required).exists():
            raise FileNotFoundError(f"Missing required cluster-build input: {required}")
    apktool = ensure_apktool(java)

    build_root = (PROBE_OUT / "engine-cluster-apk").resolve()
    if PROBE_OUT.resolve() not in build_root.parents:
        raise RuntimeError(f"Refusing to clean build directory outside probe/out: {build_root}")
    if build_root.exists():
        shutil.rmtree(build_root)
    source_root = build_root / "source"
    shutil.copytree(decoded_apk, source_root)

    _run(sys.executable, PACKAGE_ROOT / "offline_build.py", "patch", "--source", source_root)

    manifest_path = source_root / "AndroidManifest.xml"
    manifest_path.write_text(
        patch_cluster_manifest(manifest_path.read_text(encoding="utf-8"), engine_count, debuggable),
        encoding="utf-8",
    )
    smali_directory = source_root / "smali" / "com" / "supercell" / "clashroyale"
    (smali_directory / "EngineClusterApplication.smali").write_text(APPLICATION_SMALI, encoding="utf-8")
    for engine_id in range(engine_count):
        name = f"Engine{engine_id}App"
        (smali_directory / f"{name}.smali").write_text(ENGINE_SMALI.format(name=name), encoding="utf-8")

    probe_destination = source_root / "lib" / "arm64-v8a" / "libcrprobe.so"
    probe_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(probe, probe_destination)

    password = _signing_password(keytool)
    unsigned_apk = build_root / "cluster-unsigned.apk"
    aligned_apk = build_root / "cluster-aligned.apk"
    signed_apk = build_root / "cluster-signed.apk"
    _run(java, "-jar", apktool, "build", source_root, "--force", "--output", unsigned_apk)
    _run(zipalign, "-f", "-p", "4", unsigned_apk, aligned_apk)
    env_java_home = str(Path(java).resolve().parents[1])
    os.environ.setdefault("JAVA_HOME", env_java_home)
    _run(
        apksigner,
        "sign",
        "--ks",
        KEYSTORE,
        "--ks-key-alias",
        KEY_ALIAS,
        "--ks-pass",
        f"pass:{password}",
        "--key-pass",
        f"pass:{password}",
        "--out",
        signed_apk,
        aligned_apk,
    )
    _run(apksigner, "verify", "--verbose", "--print-certs", signed_apk, capture=True)

    output_apk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(signed_apk, output_apk)
    report = {
        "ok": True,
        "version": "engine-cluster-apk.v1",
        "package": PACKAGE,
        "engineProcesses": engine_count,
        "interactiveMainActivity": True,
        "probeSha256": offline_build.sha256(probe),
        "apkSha256": offline_build.sha256(output_apk),
        "apkBytes": output_apk.stat().st_size,
        "outputApk": str(output_apk.resolve()),
    }
    print(json.dumps(report, indent=2), flush=True)
    return report


def build_offline_engine(input_apk: Path, output_apk: Path, engine_count: int) -> Path:
    """Port of build_offline_engine.ps1."""
    java = Path(setting("CR_JAVA"))
    keytool = Path(setting("CR_KEYTOOL"))
    build_tools = Path(setting("CR_BUILD_TOOLS"))
    ndk = Path(setting("CR_NDK_ROOT"))
    for required in (input_apk, java, keytool, build_tools, ndk):
        if not str(required) or not Path(required).exists():
            raise FileNotFoundError(f"Missing build input/tool: '{required}'. Configure .env (see macos/env.example).")
    input_apk = input_apk.resolve()
    output_apk = output_apk.resolve()
    if input_apk == output_apk:
        raise ValueError("Output APK must not overwrite the user-provided original APK.")
    work = REPO_ROOT / "build" / f"work-{uuid.uuid4().hex}"
    decoded = work / "decoded"
    probe = Path(setting("CR_PROBE", str(DEFAULT_PROBE)))
    work.mkdir(parents=True, exist_ok=True)

    print("[1/4] Verify the user-provided original APK", flush=True)
    _run(sys.executable, PACKAGE_ROOT / "offline_build.py", "check-input", "--input-apk", input_apk)
    print("[2/4] Build the native probe from source", flush=True)
    build_probe(ndk, probe)
    print("[3/4] Decode, patch, build and sign locally", flush=True)
    apktool = ensure_apktool(java)
    _run(java, "-jar", apktool, "decode", input_apk, "--output", decoded)
    build_cluster_apk(decoded, probe, output_apk, engine_count, java, keytool, build_tools)
    print("[4/4] Verify the signed APK payload", flush=True)
    _run(sys.executable, PACKAGE_ROOT / "offline_build.py", "verify", "--apk", output_apk, "--probe", probe)
    (work / "generated.env").write_text(
        f"CR_DECODED_APK={decoded}\nCR_PROBE={probe}\nCR_OUTPUT_APK={output_apk}\n", encoding="utf-8"
    )
    print(f"Built: {output_apk}", flush=True)
    print(f"Build receipt settings: {work / 'generated.env'}", flush=True)
    return output_apk


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", help="compile libcrprobe.so with the Android NDK")
    p.add_argument("--ndk-root", type=Path, default=Path(setting("CR_NDK_ROOT") or "."))
    p.add_argument("--output", type=Path, default=Path(setting("CR_PROBE", str(DEFAULT_PROBE))))
    p = sub.add_parser("engine", help="build the offline engine APK from the original APK")
    p.add_argument("--input-apk", type=Path, default=Path(setting("CR_INPUT_APK", str(REPO_ROOT / "user-provided/nulls-royale.apk"))))
    p.add_argument("--output-apk", type=Path, default=Path(setting("CR_OUTPUT_APK", str(REPO_ROOT / "build/cr-ai-offline.apk"))))
    p.add_argument("--engine-count", type=int, default=int(setting("CR_BUILD_ENGINE_COUNT", "1")))
    args = parser.parse_args(argv)
    if args.command == "probe":
        build_probe(args.ndk_root, args.output)
    else:
        build_offline_engine(args.input_apk, args.output_apk, args.engine_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
