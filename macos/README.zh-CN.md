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

### 已在 Mac mini（M4 / 16 GB）上验证（2026-11-26）

- `production_ready=true`，引擎身份与 base-15.535.3 锁定值全部一致
- 无画面评测 General 对 IL 2 局：General 2 胜，0 个被拒动作，一局约 94 秒（约 3 倍速）
- 带画面 `offline_duel`：速猪专精对 General 正常出牌、交战、掉塔血
- 苹果芯片专有问题：原生 arm64 默认开启堆指针标记（指针最高字节 0xb4），probe 的可读性检查因此失败，`observe` 会在 `"objects":[` 处被截断。离线 APK 已在 Manifest 里关闭这项（`allowNativeHeapPointerTagging=false`），`prepare` 也会用 `am compat` 兜底

### 模型开局很被动

作者的训练历程写过：argmax 解码下 General 和速猪专精在空场上倾向于等待。实测两边会满圣水一直等到第 2 分钟双倍圣水才出手。看 AI 对打时，可以在 `--artifact-dir` 目录里 `touch force-0`（或 `force-1`）强制该方下一次决策出牌；人机对战的 `offline_agent` 默认按概率采样，加 `--deterministic` 才是 argmax。

### 人机对战

模拟器窗口显示在 Mac mini 的屏幕上，用屏幕共享或 UU 远程连过去操作：先点下方手牌，再点场地。最简单的是双击 `macos/play_vs_ai.command`（可以复制到桌面），它会重启引擎并开一局，你在下方、AI 在上方。

probe 的触控拦截器按 **1080×1920** 的固定布局识别手牌和场地（MuMu 默认分辨率），所以 AVD 必须是 1080×1920；`avd-create` 已按这个尺寸创建。旧的 720×1280 AVD 需要把 `config.ini` 里的 `hw.lcd.width/height/density` 改成 1080/1920/440 再重启。

```sh
macos/fl start
.venv/bin/python -m native_runner.training.v4.offline_agent \
  --checkpoint checkpoints/General/checkpoint-step-00000460.pt \
  --match-config macos/demo_match.json --actor-owner 0 --artifact-dir runs/agent
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
  --match-config macos/demo_match.json --actor-owner 0 --artifact-dir runs/agent
.venv/bin/python -m native_runner.training.v4.evaluate \
  --checkpoint-a checkpoints/General/checkpoint-step-00000460.pt \
  --checkpoint-b checkpoints/IL/checkpoint-step-00029396.pt --games 2 --output runs/eval
```

`offline_agent` 是人机对战：模型控制一方，你在模拟器窗口里点牌下另一方。`offline_duel` 是看两个模型对打，`evaluate` 是无画面批量评测。

## 注意

- `update-original` 会临时放开 AVD 的防火墙让原版游戏联网更新；`prepare`、`check-original`、`install`、`start` 都会重新断网（只保留到宿主机 10.0.2.2 的通道）。离线 APK 永远只在断网状态下运行，不连接任何游戏服务器。
- 模拟器默认 3 GB 内存、4 核，可在 `.env` 里用 `CR_AVD_MEMORY_MB`、`CR_AVD_CORES` 调整。机器上有别的服务时，不用就 `macos/fl avd-stop`。
- `native_runner/probe/cr_replay_probe.cpp` 在顶层补了 `<array>` 等标准头文件：原来它们第一次被包含是在匿名命名空间里（经由 `.inc` 文件），NDK 27 的 libc++ 会因此报错。
