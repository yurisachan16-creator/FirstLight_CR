#!/bin/bash
# Prepare the base training AVD on Kemove (x86_64 Android 12 + ARM translation), then export its userdata.
set -euo pipefail
W=/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight
R=$W/repo
cd "$R"
log(){ echo "[$(date +%H:%M:%S)] $*"; }
mkdir -p "$W/avd" "$W/emu-compat" runs/avd build native_runner/probe/out
# 4-engine offline APK, its build receipt and the matching probe were built on the Mac mini.
cp -n "$W/stage/cr-ai-offline-e4.apk" "$W/stage/cr-ai-offline-e4.build.json" build/
cp -n "$W/stage/libcrprobe.so" native_runner/probe/out/libcrprobe.so
if [ ! -f .env ]; then
cat > .env <<EOF
# FirstLight CR training host (Kemove). Base content profile: Null's moved to V16 in 2026-11.
CR_CONTENT_PROFILE=base-15.535.3
CR_PYTHON=$R/.venv/bin/python
CR_AI_PYTHON=$R/.venv/bin/python
CR_JAVA=$W/jdk17/bin/java
CR_ANDROID_SDK=$W/sdk
CR_ADB=$W/sdk/platform-tools/adb
CR_TRAINING_ADB=$W/sdk/platform-tools/adb
CR_TRAINING_SDK_ROOT=$W/sdk
CR_EMULATOR=$W/sdk/emulator/emulator
ANDROID_AVD_HOME=$W/avd
CR_AVD_SYSTEM_IMAGE=system-images;android-31;google_apis;x86_64
CR_AVD_NAME=fl-train-base
CR_ADB_SERIAL=emulator-5554
CR_AVD_MEMORY_MB=4096
CR_AVD_CORES=1
CR_AVD_DATA_SIZE=12G
CR_EMULATOR_GPU=swiftshader_indirect
CR_EMULATOR_HEADLESS=true
CR_INPUT_APK=$R/user-provided/nulls-royale.apk
CR_OUTPUT_APK=$R/build/cr-ai-offline-e4.apk
CR_TRAINING_APK=$R/build/cr-ai-offline-e4.apk
CR_PROBE=$R/native_runner/probe/out/libcrprobe.so
EOF
fi
export JAVA_HOME=$W/jdk17
FL=".venv/bin/python macos/firstlight.py"
A=$W/sdk/platform-tools/adb

log "create AVD"; [ -d "$W/avd/fl-train-base.avd" ] || $FL avd-create
log "boot (headless)"; $FL avd-start --headless
log "ABIs: $($A -s emulator-5554 shell getprop ro.product.cpu.abilist)"
log "install original"; $FL install-original
log "seed base content"; $FL seed-base-content
log "check original"; $FL check-original
log "install offline e4 APK + start engine"; $FL install
log "single-engine readiness"
.venv/bin/python -m native_runner.training.v4.configure_emulator_engine_guests \
  --serials emulator-5554 --host-bases 26789 --engines 1 --output runs/guest-ready.json
.venv/bin/python - <<'EOF'
import json
value = json.load(open("runs/guest-ready.json"))
print(json.dumps(value, indent=1)[:1500])
EOF
log "shutdown + export userdata"
$FL avd-stop
ls -la "$W/avd/fl-train-base.avd" | grep -i userdata
SRC="$W/avd/fl-train-base.avd/userdata-qemu.img"
[ -f "$SRC.qcow2" ] && SRC="$SRC.qcow2"
$W/sdk/emulator/qemu-img convert -O raw "$SRC" "$W/prepared-userdata.img"
ls -la "$W/prepared-userdata.img"
log "PREP DONE"
