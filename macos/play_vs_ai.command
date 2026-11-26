#!/bin/zsh
# Double-click in Finder to play one human-vs-AI match in the emulator window.
# You control the bottom side; the model controls the top side.
# Optional: FIRSTLIGHT_CHECKPOINT / FIRSTLIGHT_MATCH to choose another model or deck file.
REPO=${FIRSTLIGHT_REPO:-$HOME/项目/FirstLight_CR}
CHECKPOINT=${FIRSTLIGHT_CHECKPOINT:-checkpoints/General/checkpoint-step-00000460.pt}
MATCH=${FIRSTLIGHT_MATCH:-macos/demo_match.json}
cd "$REPO" || { echo "找不到仓库：$REPO"; read; exit 1; }

echo "正在启动离线引擎（约 30 秒）……"
if ! macos/fl start > runs/avd/play-start.log 2>&1; then
  echo "引擎启动失败，日志：$REPO/runs/avd/play-start.log"; read; exit 1
fi

RUN=runs/human-$(date +%Y%m%d-%H%M%S)
echo
echo "对局开始！你在【下方】，AI（$(basename "$(dirname "$CHECKPOINT")")）在【上方】。"
echo "操作：先点下方的一张手牌，再点场地上要放的位置。"
echo "把这个终端窗口挪开，切到模拟器窗口去打。想提前结束就在这里按 Ctrl+C。"
echo
.venv/bin/python -m native_runner.training.v4.offline_agent \
  --checkpoint "$CHECKPOINT" --match-config "$MATCH" --actor-owner 0 \
  --device cpu --artifact-dir "$RUN" > "$RUN.log" 2>&1
python3 - "$RUN/result.json" <<'EOF'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except Exception:
    print("没有拿到对局结果，日志见同名 .log 文件"); raise SystemExit
info = r.get("info", {}).get("1", {})
print(f"对局结束：tick={r.get('tick')} AI 出牌 {r.get('model_actions')} 次，被拒 {r.get('rejected_actions')} 次")
print("结果原文：", json.dumps({k: r.get(k) for k in ('terminated', 'truncated', 'stopped', 'rewards')}, ensure_ascii=False))
EOF
echo "按回车关闭窗口。"; read
