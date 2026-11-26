#!/bin/bash
# Waits for the repository clone, then builds the Python env and fetches the original APK.
set -euo pipefail
W=/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight
cd "$W"
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "waiting for repository clone"
while pgrep -f "clone -q -b macos-avd" >/dev/null || [ ! -f repo/requirements.txt ]; do sleep 10; done
cd repo
log "Python venv (torch cu128 for Blackwell)"
[ -x .venv/bin/python ] || /usr/bin/python3.12 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
grep -v '^torch' requirements.txt > /tmp/fl-req.txt
.venv/bin/pip install -q -r /tmp/fl-req.txt pytest huggingface_hub
CUDA_VISIBLE_DEVICES=3 .venv/bin/python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.cuda.get_device_name(0),torch.cuda.get_device_capability(0))"
log "original APK (Null's official endpoint)"
mkdir -p user-provided
[ -f user-provided/nulls-royale.apk ] || curl -fsSL -o user-provided/nulls-royale.apk 'https://tempweb.nullsusercontent.com/fpapk/nr_15.535.13_release_ff1c6c29.apk'
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from native_runner.offline_build import original_apk_problem
p = original_apk_problem('user-provided/nulls-royale.apk'); print('original APK:', p or 'OK (libg matches)')"
log "check_install"
CUDA_VISIBLE_DEVICES=3 .venv/bin/python tools/check_install.py 2>&1 | tail -2
log "PY DONE"
