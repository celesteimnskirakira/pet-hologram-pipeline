# Pet Hologram Pipeline

将一张宠物照片转换为可在全息展示设备上循环播放的黑底动态宠物影像。

本仓库包含从 **宠物特征提取、黑底图生成、睡姿循环视频生成、全息画面合成，到 USB 推送至 ESP32-S3 AMOLED 屏幕** 的完整原型链路，同时提供命令行工具、网页界面、路演展示模式和 macOS 上传器。

## 项目用途

该项目用于“跟屁宠”全息 3D 桌宠原型：访客或宠物主人上传猫狗照片，系统保留宠物的花色、五官、项圈及不对称特征，生成纯黑背景的睡眠动画，并在 Pepper's Ghost 亚克力结构中呈现悬浮效果。

完整流程：

```text
宠物照片
  ↓
视觉模型提取宠物特征
  ↓
生成纯黑背景正视图
  ↓
生成黑底睡姿桥接图
  ↓
生成 5 秒首尾循环视频
  ↓
接缝检测与循环修整
  ↓
单面 / 四面全息画面合成
  ↓
USB 推送至 AMOLED 设备循环播放
```

## 核心能力

- 猫狗照片自动识别与结构化特征提取
- 尽量保留花色、斑纹、眼睛、耳型、鼻色、白爪和项圈等身份特征
- 黑色背景自动检测、重试与近黑像素压黑
- 三种睡姿：侧卧蜷睡、趴卧收爪、侧身伸展
- 5 秒低运动睡眠视频生成
- 首尾帧接缝、画面运动量检测与 FFmpeg 循环修整
- 单面亚克力和四面全息锥两种输出布局
- 命令行、桌面网页、局域网扫码路演三种入口
- macOS 视频转换、管理及 USB 推送工具
- Ark 主通道与 Agnes 备用通道
- 每次生成保留提示词、特征、质量指标、成本和任务信息

## 仓库结构

```text
.
├── petloop/                 # 流水线核心：配置、模型调用、图像处理、循环检测
├── cli.py                   # 命令行入口
├── server.py                # 本机网页上传界面
├── display.py               # 局域网扫码与路演展示模式
├── local_booth/             # 独立本地体验台：扫码、排队、生成、合成、USB 推送
├── holo-video-uploader/     # macOS 上传器及 ESP32-S3 播放器相关代码
├── tests/                   # 流水线测试
├── .env.example             # API 配置示例
└── runs/                    # 运行产物，首次执行后生成
```

## 环境要求

基础流水线：

- Python 3.10 或更高版本
- [FFmpeg](https://ffmpeg.org/)
- Pillow

扫码和本地体验台还需要：

- segno
- pyserial

macOS 可使用 Homebrew 安装：

```bash
brew install ffmpeg
python3 -m pip install Pillow segno pyserial
```

## 配置 API

复制环境变量示例：

```bash
cp .env.example .env
```

在 `.env` 中填写火山方舟 API Key：

```dotenv
ARK_API_KEY=your_api_key
```

也可以配置 Agnes 作为备用通道：

```dotenv
AGNES_API_KEY=your_api_key
```

`.env` 已被 Git 忽略，请勿将真实密钥提交到仓库。

默认模型配置位于 `petloop/config.py`：

| 用途 | 默认模型 |
| --- | --- |
| 宠物特征提取 | `doubao-seed-1-6-flash-250828` |
| 黑底图片生成 | `doubao-seedream-4-0-250828` |
| 循环视频生成 | `doubao-seedance-2-0-mini-260615` |

模型 ID 可通过 `ARK_VISION_MODEL`、`ARK_IMAGE_MODEL` 和 `ARK_VIDEO_MODEL` 环境变量覆盖。

## 快速开始

先检查 API、代理、网络和 FFmpeg：

```bash
python3 cli.py doctor
```

运行完整流水线：

```bash
python3 cli.py run path/to/pet.jpg
```

默认输出为：

- 宠物类型自动识别
- 侧卧蜷睡
- 480p、1:1
- 5 秒循环视频
- `trim` 接缝修整
- Ark 模型通道

仅估算、不调用生成 API：

```bash
python3 cli.py run path/to/pet.jpg --dry-run
```

## 命令行用法

```bash
# 环境自检
python3 cli.py doctor

# 查看当前视频成本估算
python3 cli.py price --seconds 5

# 完整生成
python3 cli.py run pet.jpg

# 仅生成黑底正视图
python3 cli.py still pet.jpg

# 从已有图片生成循环视频
python3 cli.py loop still.png --traits traits.json

# 检测已有视频的接缝和运动量
python3 cli.py seam video.mp4
```

常用参数：

| 参数 | 可选值 | 说明 |
| --- | --- | --- |
| `--provider` | `ark`、`agnes` | 模型服务通道 |
| `--pet` | `auto`、`cat`、`dog` | 宠物类型 |
| `--pose` | `curled_side`、`loaf`、`sprawl` | 睡姿 |
| `--resolution` | `480p`、`720p` | 输出分辨率 |
| `--ratio` | `1:1`、`16:9`、`9:16`、`4:3`、`3:4` | 画幅 |
| `--loop-mode` | `trim`、`xfade`、`none` | 循环修整方式 |
| `--no-sleep-bridge` | — | 跳过睡姿桥接图 |
| `--out` | 目录路径 | 指定输出目录 |

示例：

```bash
python3 cli.py run dog.jpg \
  --pet dog \
  --pose loaf \
  --resolution 480p \
  --ratio 1:1 \
  --loop-mode trim
```

## 使用方式

### 1. 本机网页界面

适合开发和单次生成：

```bash
python3 server.py --port 8791
```

浏览器打开 `http://127.0.0.1:8791`。页面提供照片上传、参数设置、实时日志、黑底图预览和循环视频预览。

该服务没有公网鉴权，默认仅供本机使用，请勿直接暴露到互联网。

### 2. 局域网路演模式

适合展台扫码上传及投影展示：

```bash
python3 display.py --port 8792 --lan
```

主要页面：

| 路径 | 用途 |
| --- | --- |
| `/u?k=...` | 访客手机上传页 |
| `/` | 展台操作台与任务队列 |
| `/stage` | 纯黑背景全屏展示页 |

手机与电脑需要连接同一 Wi-Fi。建议现场使用私人热点；访客链接虽然带随机 token，但操作台没有完整的公网级鉴权。

### 3. 独立本地体验台

`local_booth/` 将现场链路整合为一个独立入口：

```text
扫码上传 → Mac 本地排队 → 联网调用模型 → 四面视频合成 → USB 推送设备
```

首次使用：

1. 配置根目录的 `.env`。
2. 安装 FFmpeg。
3. 双击 `local_booth/setup_local_booth.command`。
4. USB 连接已刷入播放器固件的设备。
5. 双击 `local_booth/Local Holo Booth.app`。

详细说明见 [local_booth/README.md](local_booth/README.md)。

## Holo Video Uploader

`holo-video-uploader/` 提供 Apple Silicon macOS 应用，用于把普通视频自动处理并推送至：

**Waveshare ESP32-S3-Touch-AMOLED-1.75**

处理流程：

```text
MP4 / MOV / AVI 等视频
  ↓
可选四面全息布局
  ↓
360×360 / 10 FPS / MJPEG AVI / 无音频
  ↓
USB 上传至 microSD
  ↓
466×466 全屏循环播放
```

上传器支持本地拖放、视频列表切换，以及轮询团队后端自动接收新 MP4。详细安装、固件和后端协议见 [holo-video-uploader/README.md](holo-video-uploader/README.md)。

## 输出产物

每次运行默认在 `runs/<timestamp>-run/` 中生成：

```text
step1_source.png             # 规范化输入图
traits.json                  # 宠物结构化特征
step2_front_view_black.png   # 黑底正视图
step2b_sleep_pose_black.png  # 黑底睡姿桥接图
step3_raw.mp4                # 模型原始视频
step3_loop_5s.mp4            # 修整后的循环视频
preview_loop.gif             # 快速预览
prompt_still.txt             # 正视图提示词
prompt_sleep_still.txt       # 睡姿图提示词
prompt_video.txt             # 视频提示词
report.json                  # 成本、质量指标与任务信息
```

失败重试生成的中间图片也会保留，便于比较和调试。

## 全息显示说明

本项目面向 Pepper's Ghost 亚克力反射结构，因此黑底不是单纯的视觉风格，而是显示条件：黑色区域在反射中接近透明，亮色宠物主体看起来悬浮在空间中。

输出阶段支持：

- 左右预翻转，抵消 45° 亚克力反射造成的镜像
- 可选垂直翻转
- 近黑像素压至纯黑，减少屏幕背光雾感
- 主体留边，降低亚克力边缘失真
- `single` 单面反射和 `quad` 四面锥布局

不同屏幕和亚克力角度仍需要真机校准。

## 测试

运行主流水线测试：

```bash
python3 -m unittest discover -s tests -v
```

运行本地体验台测试：

```bash
python3 local_booth/test_local_booth.py
```

部分测试会调用 FFmpeg；未配置真实 API Key 时，测试不会替代完整的真机与模型效果验证。

## 已知限制

- 生成结果仍受模型随机性影响，首轮使用应检查 `preview_loop.gif`。
- 接缝和运动量指标只能判断是否循环自然、画面是否运动，不能判断睡姿或宠物身份是否完全正确。
- 黑底质量和宠物特征保留已做自动约束，但极端构图仍可能需要重试。
- API 价格、模型可用性和限流规则会变化，请在批量生成前运行 `python3 cli.py price` 并核对服务商控制台。
- 本地网页及扫码模式是原型级工具，不应未经鉴权直接部署到公网。
- USB 推送要求设备运行兼容的 `UPLOAD2` 固件，且同一时间只能由一个应用占用串口。

## 许可证

当前仓库尚未包含开源许可证。除非仓库所有者另行授权，代码默认保留全部权利。
