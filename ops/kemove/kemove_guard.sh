#!/bin/bash
# Yield guard for FirstLight PPO on Kemove: web users of apimain.site come first.
# Every 5 s: if any ComfyUI instance (8188-8191) has a running or pending job, or
# MemAvailable drops below GUARD_MIN_AVAILABLE_GB, stop the trainer and every training
# emulator immediately (frees ~50 GB within seconds) and tell the main watchdog to stop.
set -u
R=/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight/repo
MIN_GB=${GUARD_MIN_AVAILABLE_GB:-75}
LOG=$R/runs/guard.log
log(){ echo "[$(date +%H:%M:%S)] $*" >> "$LOG"; }
busy_queues(){
  local total=0 n
  for port in 8188 8189 8190 8191; do
    n=$(curl -s --noproxy '*' -m 3 "http://127.0.0.1:$port/queue" \
      | python3 -c 'import sys,json;d=json.load(sys.stdin);print(len(d.get("queue_running",[]))+len(d.get("queue_pending",[])))' 2>/dev/null)
    [[ $n =~ ^[0-9]+$ ]] && total=$((total + n))
  done
  echo "$total"
}
log "guard start: yield on any ComfyUI job, or MemAvailable < ${MIN_GB} GiB"
while true; do
  [ -f "$R/runs/GUARD_OFF" ] && { log "guard off"; exit 0; }
  if ! pgrep -f "^bash /tmp/kemove_train.sh" >/dev/null; then log "training script gone; guard exits"; exit 0; fi
  avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  jobs=$(busy_queues)
  reason=""
  (( jobs > 0 )) && reason="web users have $jobs ComfyUI job(s)"
  (( avail < MIN_GB )) && reason="${reason:+$reason; }MemAvailable ${avail} GiB < ${MIN_GB} GiB"
  if [ -n "$reason" ]; then
    log "YIELD: $reason"
    touch "$R/runs/STOP_TRAINING"
    [ -d "$R/runs/node0" ] && touch "$R/runs/node0/STOP_ENGINES"
    pkill -TERM -f "native_runner.training.v4.train_ppo_self_play_cluster"
    pkill -TERM -f "native_runner.training.v4.remote_ppo_worker_server"
    sleep 3
    pkill -KILL -f "^/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight/sdk/emulator/qemu/linux-x86_64/qemu-system-x86_64.*crppo_"
    sleep 5
    log "after yield: MemAvailable=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo) GiB"
    exit 0
  fi
  sleep 5
done
