# Holo Video Uploader

用于已经刷入 Holo Player 固件的 **Waveshare ESP32-S3-Touch-AMOLED-1.75**。

把视频拖进 macOS 窗口，应用会自动完成：

```text
普通视频
→ 360×360 / 10 FPS / MJPEG AVI / 无音频
→ USB 上传到设备 microSD
→ AMOLED 466×466 全屏循环播放
```

不需要拔出 microSD，也不需要手工运行 FFmpeg。

## 直接下载使用

1. 下载 [`dist/Holo-Video-Uploader-macOS-arm64.zip`](dist/Holo-Video-Uploader-macOS-arm64.zip)。
2. 解压，将 `Holo Video Uploader.app` 拖到“应用程序”。
3. 第一次启动时右键应用并选择“打开”。
4. 用支持数据传输的 USB 线连接已经刷好播放器固件的微雪设备。
5. 将 MP4、MOV、M4V、AVI、MKV 或 WebM 拖进窗口。

应用会自动转换、上传并开始播放。转换后的文件保存在：

```text
~/Movies/Holo Player/
```

窗口会列出这些视频。选中一个视频，点击“播放到设备”，即可切换微雪当前播放内容。

### 系统要求

- Apple Silicon Mac（M1/M2/M3/M4 或更新）
- macOS 13 或更新
- Waveshare ESP32-S3-Touch-AMOLED-1.75 已刷入配套播放器固件
- microSD 已插在设备中
- USB 数据线；仅供电的线无法上传

预编译包内置 FFmpeg，不需要安装 Python、Homebrew 或其他运行环境。

SHA-256：

```text
86731fe189b64582dc6633424c148de719478172bff9293eb1f2a304dc7096a2
```

## 从源码构建

需要 Xcode Command Line Tools 和 FFmpeg：

```bash
brew install ffmpeg
./mac_uploader/build_app.sh
```

如果 FFmpeg 不在 `PATH` 中：

```bash
FFMPEG_PATH=/absolute/path/to/ffmpeg ./mac_uploader/build_app.sh
```

成功后应用位于：

```text
Holo Video Uploader.app
```

## 工作方式

- 应用自动查找 `/dev/cu.usbmodem*` 设备。
- USB 串口速度为 115200。
- 每次上传会原子替换设备上的 `/video/current.avi`。
- 上传完成后设备立即重新打开视频，并在 EOF 后自动循环。
- 视频列表保存在 Mac 上；选择播放时会把对应 AVI 重新上传到设备。

应用不使用网络，不会上传视频到云端。
