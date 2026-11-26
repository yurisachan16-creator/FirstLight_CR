# Kemove 单机 PPO 训练脚本（2026-11-26 首次跑通）

在一台没有 Slurm 的 x86_64 Linux GPU 服务器上，用 3 台 Android 12 x86_64 AVD × 4 个引擎（96 局并发）继续训练 General。路径写死在 `/mnt/7adf…/firstlight`，换机器要改 `W=`。

| 文件 | 作用 |
| --- | --- |
| `kemove_sdk.sh` / `kemove_py.sh` | 装 JDK 17、安卓 SDK（x86_64 镜像自带 ARM 转译），建 Python 环境（Blackwell 显卡需要 torch 2.7.1+cu128），下载原版 APK |
| `kemove_guest_prep.sh` | 准备模板模拟器、放入基础资源、安装离线 APK、做就绪检查、导出 userdata |
| `kemove_train.sh` | 写 `.env` 里的 PPO 配置 → 卡组池 → 引擎节点（taskset 400-463）→ 拓扑 → 远程采集服务（127.0.1.1:36900）→ GPU3 上的训练器 → 看门狗（时间预算 / 内存下限） |
| `kemove_guard.sh` | 让路守卫：8188–8191 只要有网页用户的任务，或者可用内存低于 75GB，就立刻停掉训练 |
| `summarize_matches.py` | 统计训练对局里学习者对各类对手的胜率和平局率 |

踩过的坑（仓库里都已修好）：端口 28003 被占用 → 改用 36000；模拟器 37.x 下克隆的数据解不开 → 开机后再放资源；训练器只认 7,695/20 的正式卡组池 → 用 `--allow-unverified-deck-pool`；卡组里有 15.535.86 才有的卡 → `--exclude-card-id 26000106`；作者脚本没有启动远程采集服务 → 要自己起；冷启动超过 180 秒 → `CR_PPO_ENGINE_READY_TIMEOUT=480`。

第 1 次训练（83 轮）：越练越被动（自对弈平局率 3% → 52%），对原版 General：第 40 轮 2:8，第 80 轮 4:2。下次先打开圣水溢出惩罚。
