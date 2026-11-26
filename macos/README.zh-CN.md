# 在 Mac（Apple 芯片）上运行 FirstLight CR

官方流程是 Windows + MuMu。这里把它换成 **Android Emulator 的 Android 12 arm64 AVD**：Apple 芯片原生跑 ARM64，不需要转译。训练（IL/PPO）仍然需要 Linux + NVIDIA 显卡，本目录只负责“引擎机”：构建离线 APK、启动离线引擎、人机对战和模型评测。

| Windows 脚本 | Mac 命令 |
| --- | --- |
| `check_original_game.ps1` | `macos/fl check-original` |
| `build_offline_engine.ps1` | `macos/fl build` |
| `install_offline_engine.ps1` | `macos/fl install` |
| `native_runner/start_offline.ps1` | `macos/fl start` |
| （MuMu 管理器） | `macos/fl avd-create / avd-start / avd-stop` |

## 需要的东西

- Python 3.12（带 Tkinter），在仓库根目录建 `.venv` 并安装 `requirements.txt`
- JDK 17、Android SDK：`cmdline-tools/latest`、`platform-tools`、`emulator`、`system-images;android-31;google_apis;arm64-v8a`、`build-tools;37.0.0`、`ndk;27.3.13750724`
- 你自己提供的原版 `user-provided/nulls-royale.apk`（Null's Royale 15.535.13 arm64，SHA-256 见 `native_runner/supported_engine.json`）

把 `macos/env.example` 里的配置抄进仓库根目录的 `.env`，改成本机的绝对路径。然后运行 `macos/fl doctor` 检查。

## 2026 年 11 月之后：用基础资源档案

Null's 私服 11 月初升级到 V16，15.535.13 客户端会被强制更新，服务器也不再下发 15.535.86 资源包，所以下面原流程里的 `update-original` 已经走不通。改用 **`base-15.535.3` 档案**：

- 在 `.env` 里加 `CR_CONTENT_PROFILE=base-15.535.3`
- 原版 APK 仍是 15.535.13；整包 SHA-256 每次下载都带随机水印，现在只要 `lib/arm64-v8a/libg.so` 与锁定值一致即可（`110aa2b5…`）
- `seed-base-content` 把 APK 自带的 `assets/fingerprint.json`、`assets/assets.scdb` 放进 `update/`，全程不联网；规则集改为锁定这两个文件和 15.535.3 的版本号，引擎上报的身份对不上仍会拒绝
- 对局默认地图改为 15000002（PvP_goblin），因为 15000199 只存在于 15.535.86
- 代价：卡牌数值是 15.535.3 的，和模型训练时的 15.535.86 有小差别；15.535.86 才加入的卡牌/形态不可用

```sh
macos/fl avd-start
macos/fl install-original
macos/fl seed-base-content      # 代替 update-original
macos/fl check-original
macos/fl build
macos/fl install
```

## 步骤（原 15.535.86 档案）

```sh
macos/fl doctor                 # 看缺什么
macos/fl probe                  # 只编译 probe，验证 NDK 工具链（不需要 APK）
macos/fl avd-create             # 建专用 AVD：Android 12、3 GB 内存、4 核
macos/fl avd-start              # 开机（有窗口）；加 --headless 不开窗口

# 1. 装原版游戏，联网更新到 15.535.86
macos/fl install-original
macos/fl update-original        # 自动打开游戏，等资源指纹对上后关闭；进度截图在 runs/avd/update-latest.png

# 2. 断网检查 → 构建 → 安装离线引擎
macos/fl check-original
macos/fl build
macos/fl install                # 安装后自动启动引擎，失败会自动还原

# 3. 用起来
macos/fl start                  # 以后每次开机后重启引擎
.venv/bin/python -m native_runner.training.v4.offline_agent \
  --checkpoint checkpoints/General/checkpoint-step-00000460.pt \
  --match-config native_runner/data/native_match.json --actor-owner 0 --artifact-dir runs/agent
.venv/bin/python -m native_runner.training.v4.evaluate \
  --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt --games 2 --output runs/eval
```

`offline_agent` 是人机对战：模型控制一方，你在模拟器窗口里点牌下另一方。`offline_duel` 是看两个模型对打，`evaluate` 是无画面批量评测。

## 注意

- `update-original` 会临时放开 AVD 的防火墙让原版游戏联网更新；`prepare`、`check-original`、`install`、`start` 都会重新断网（只保留到宿主机 10.0.2.2 的通道）。离线 APK 永远只在断网状态下运行，不连接任何游戏服务器。
- 模拟器默认 3 GB 内存、4 核，可在 `.env` 里用 `CR_AVD_MEMORY_MB`、`CR_AVD_CORES` 调整。机器上有别的服务时，不用就 `macos/fl avd-stop`。
- `native_runner/probe/cr_replay_probe.cpp` 在顶层补了 `<array>` 等标准头文件：原来它们第一次被包含是在匿名命名空间里（经由 `.inc` 文件），NDK 27 的 libc++ 会因此报错。
