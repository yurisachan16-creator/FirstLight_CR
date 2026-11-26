#!/bin/bash
# FirstLight CR: continue PPO training of General on Kemove (single node, no Slurm).
# 12 engines = 3 AVD guests x 4 engines (8 lanes each -> 96 concurrent matches), trainer on GPU3.
# A watchdog stops everything when MemAvailable < 60 GiB (protect web users) or after the time budget.
set -uo pipefail
W=/mnt/7adf615d-d42f-403e-bf21-537b5e6960dd/firstlight
R=$W/repo
cd "$R"
log(){ echo "[$(date +%H:%M:%S)] $*"; }
export SLURM_JOB_ID=fllocal1
BUDGET_SECONDS=${BUDGET_SECONDS:-7200}
MIN_AVAILABLE_GB=${MIN_AVAILABLE_GB:-60}
RUN=$R/runs/ppo-general-$(date +%Y%m%d-%H%M)
NODE=$R/runs/node0
mkdir -p runs

if ! grep -q '^CR_SOURCE_AVD=' .env; then
cat >> .env <<EOF
# PPO engine node (single host)
CR_SOURCE_AVD=$W/avd/fl-train-base.avd
CR_SOURCE_AVD_INI=$W/avd/fl-train-base.ini
CR_EMULATOR_INITDATA=$W/prepared-userdata.img
CR_EMULATOR_COMPAT=$W/emu-compat
CR_PPO_ENGINES_PER_GUEST=4
CR_PPO_GUEST_MEMORY_MB_PER_ENGINE=4096
CR_PPO_BASE_PORT=36000
CR_PPO_REMOTE_WORKER_PORT=36900
CR_EMULATOR_BASE_PORT=5560
CR_PPO_CPU_OFFSET=0
CR_PPO_DECK_POOL=$R/runs/deck-pool.json
CR_PPO_CHECKPOINT=$R/checkpoints/General/checkpoint-step-00000460.pt
EOF
fi

log "deck pool (background; kept if already built and filtered for base-15.535.3)"
(
  [ -f runs/deck-pool.json ] && { echo "deck pool exists: $(.venv/bin/python -c 'import json;print(len(json.load(open("runs/deck-pool.json"))["decks"]))') decks"; exit 0; }
  .venv/bin/python - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download("VanguardX101/IL_Replay", repo_type="dataset", allow_patterns=["replays/*", "README.md"],
                  local_dir="datasets/IL_Replay")
EOF
  .venv/bin/python -m native_runner.training.v4.build_ppo_deck_pool \
    --dataset-root ./datasets --output runs/deck-pool.json --minimum-uses 5
) > runs/deck-pool.log 2>&1 &
DECK_PID=$!

log "engine node: 12 engines on CPUs 400-463"
rm -rf "$NODE"
setsid nohup taskset -c 400-463 bash native_runner/training/v4/launch_ppo_engine_node.sh "$NODE" 0 12 \
  > runs/node0.log 2>&1 < /dev/null &
for i in $(seq 1 180); do
  [ -f "$NODE/engine-node-ready.json" ] && break
  if ! pgrep -f launch_ppo_engine_node.sh >/dev/null; then log "engine node exited early"; tail -30 runs/node0.log; exit 1; fi
  sleep 10
done
[ -f "$NODE/engine-node-ready.json" ] || { log "engine node not ready after 30 min"; tail -30 runs/node0.log; exit 1; }
log "engine node ready; MemAvailable=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo) GiB"

wait "$DECK_PID" || { log "deck pool failed"; tail -20 runs/deck-pool.log; touch "$NODE/STOP_ENGINES"; exit 1; }
tail -3 runs/deck-pool.log

log "topology"
.venv/bin/python -m native_runner.training.v4.build_ppo_multinode_topology \
  --node-ready "$NODE/engine-node-ready.json" --output runs/topology.json \
  --trainer-job-id "$SLURM_JOB_ID" --trainer-host "$(hostname -s)" --gpus 1 \
  --worker-cpus 464-495 --control-cpus 496-503

log "remote PPO worker service on 127.0.1.1:36900 (CPUs 412-423)"
(
  export PYTHONPATH=$R PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MALLOC_ARENA_MAX=2
  exec taskset -c 412-423 .venv/bin/python -m native_runner.training.v4.remote_ppo_worker_server \
    --bind-host 127.0.1.1 --port 36900 --global-start 0 --engine-count 12 --engine-base-port 36000 \
    --preload-static-catalogs
) > runs/worker-server.log 2>&1 < /dev/null &
WORKER_PID=$!
for i in $(seq 1 60); do
  ss -ltnH "sport = :36900" | grep -q . && break
  kill -0 "$WORKER_PID" 2>/dev/null || { log "worker service exited"; tail -20 runs/worker-server.log; touch "$NODE/STOP_ENGINES"; exit 1; }
  sleep 2
done
ss -ltnH "sport = :36900" | grep -q . || { log "worker service not listening"; tail -20 runs/worker-server.log; touch "$NODE/STOP_ENGINES"; exit 1; }
log "worker service listening"

log "trainer on GPU3 -> $RUN (log: $RUN.trainer.log; the trainer requires an empty output dir)"
(
  export PYTHONPATH=$R PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MALLOC_ARENA_MAX=2
  export CUDA_VISIBLE_DEVICES=3
  exec taskset -c 464-511 .venv/bin/python -m torch.distributed.run --standalone --nproc-per-node=1 \
    -m native_runner.training.v4.train_ppo_self_play_cluster \
    --deck-pool runs/deck-pool.json --allow-unverified-deck-pool \
    --checkpoint checkpoints/General/checkpoint-step-00000460.pt \
    --il-anchor-checkpoint checkpoints/IL/checkpoint-step-00029396.pt \
    --il-opening-force-after-seconds 15 \
    --self-play-percent 50 --fixed-opponent-percent 50 \
    --fixed-opponent-checkpoint "checkpoints/active IL/checkpoint-step-00000030.pt" \
    --fixed-opponent-checkpoint checkpoints/General/checkpoint-step-00000460.pt \
    --fixed-opponent-il-max-average-elixir 3.0 \
    --output-dir "$RUN" --engine-ready runs/topology.json --timeout 600 \
    --updates 10000 --rollout-segment-seconds 40 --snapshot-every 2 \
    --ppo-epochs 1 --lane-minibatch-size 96 --lane-microbatch-size 48 --sequence-chunk-steps 16 \
    --seed 20261126
) > "$RUN.trainer.log" 2>&1 < /dev/null &
TRAINER_PID=$!
echo "$TRAINER_PID" > runs/trainer.pid

log "watchdog: budget ${BUDGET_SECONDS}s, stop if MemAvailable < ${MIN_AVAILABLE_GB} GiB"
START=$(date +%s)
reason=""
while kill -0 "$TRAINER_PID" 2>/dev/null; do
  avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
  now=$(date +%s)
  echo "$(date +%H:%M:%S) avail=${avail}GiB elapsed=$((now-START))s" >> runs/watchdog.log
  if (( avail < MIN_AVAILABLE_GB )); then reason="memory ${avail}GiB < ${MIN_AVAILABLE_GB}GiB"; break; fi
  if (( now - START >= BUDGET_SECONDS )); then reason="time budget reached"; break; fi
  [ -f runs/STOP_TRAINING ] && { reason="manual stop"; break; }
  sleep 15
done
[ -z "$reason" ] && reason="trainer exited"
log "stopping: $reason"
pkill -TERM -f train_ppo_self_play_cluster 2>/dev/null
sleep 20
pkill -KILL -f train_ppo_self_play_cluster 2>/dev/null
kill -TERM "$WORKER_PID" 2>/dev/null
sleep 5
kill -KILL "$WORKER_PID" 2>/dev/null
touch "$NODE/STOP_ENGINES"
for i in $(seq 1 30); do pgrep -f "qemu-system-x86_64.*crppo|launch_ppo_engine_node" >/dev/null || break; sleep 5; done
log "after stop: MemAvailable=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo) GiB"
ls -la "$RUN" | tail -8
log "TRAIN SCRIPT DONE ($reason)"
