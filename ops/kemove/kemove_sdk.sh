#!/bin/bash
# JDK 17 + Android SDK (x86_64 Android 12 image with ARM translation) for FirstLight PPO guests.
set -euo pipefail
W=/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight
cd "$W"
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "JDK 17"
if [ ! -x jdk17/bin/java ]; then
  curl -fsSL -o jdk17.tgz 'https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse'
  mkdir -p jdk17 && tar -xzf jdk17.tgz -C jdk17 --strip-components=1 && rm jdk17.tgz
fi
export JAVA_HOME="$W/jdk17"; "$JAVA_HOME/bin/java" -version 2>&1 | head -1
log "Android SDK"
if [ ! -x sdk/cmdline-tools/latest/bin/sdkmanager ]; then
  curl -fsSL -o clt.zip https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip
  mkdir -p sdk/cmdline-tools && python3 -c "import zipfile;zipfile.ZipFile('clt.zip').extractall('sdk/cmdline-tools')" \
    && mv sdk/cmdline-tools/cmdline-tools sdk/cmdline-tools/latest && rm clt.zip
  chmod +x sdk/cmdline-tools/latest/bin/*
fi
SM="$W/sdk/cmdline-tools/latest/bin/sdkmanager"
yes | "$SM" --sdk_root="$W/sdk" --licenses >/dev/null 2>&1 || true
"$SM" --sdk_root="$W/sdk" "platform-tools" "emulator" "system-images;android-31;google_apis;x86_64" 2>&1 | grep -vE '^\[|Loading' | tail -3
chmod +x sdk/emulator/emulator sdk/emulator/qemu/linux-x86_64/* sdk/platform-tools/adb 2>/dev/null || true
ls sdk; ls sdk/system-images/android-31/google_apis/x86_64 | head -5
log "SDK DONE"
