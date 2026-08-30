# Holo Video Uploader

用于已经刷入 Holo Player 固件的 **Waveshare ESP32-S3-Touch-AMOLED-1.75**。

把视频拖进 macOS 窗口，或者从团队后端接收 MP4，应用会自动完成：

```text
普通视频
→ 默认拼合为头朝外的四面全息布局
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

### 四面全息布局

“新视频布局”默认选择 **四面全息（头朝外）**：单画面视频会被复制、旋转并拼成上、下、左、右四个方向，适配当前实测棱锥的反射方向。

```text
           正向
             ↑
反向  ←   黑色中心   →  反向
             ↓
           倒向
```

如果同事的后端已经返回拼合完成的四面 MP4，请改选 **单画面全屏**，避免再次拼合成 16 份。

## 从后端自动接收 MP4

1. “后端 MP4 接收”默认使用正式接口
   `https://genpichong.dpdns.org/api/device/next`。
2. 如果接口需要鉴权，填写 Bearer Token；Token 只在本次运行中保留。
3. 点击“从后端接收”进行一次接收，或勾选“自动接收（每 10 秒）”。
4. 检测到新 ID 后，应用自动下载 MP4、转换、USB 上传并切换播放。

应用支持接口直接返回 `video/mp4`，也支持返回包含 `id`、`download_url` 和可选 `ack_url` 的 JSON。完整协议见 [`BACKEND_API.md`](BACKEND_API.md)。

### 系统要求

- Apple Silicon Mac（M1/M2/M3/M4 或更新）
- macOS 13 或更新
- Waveshare ESP32-S3-Touch-AMOLED-1.75 已刷入配套播放器固件
- microSD 已插在设备中
- USB 数据线；仅供电的线无法上传

预编译包内置 FFmpeg，不需要安装 Python、Homebrew 或其他运行环境。

SHA-256：

```text
f65d8679133932bb2b566cd235a3b832bc6c2a99f6f6e57a93512843a46cd9cb
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
- 1.2 使用可靠的 `UPLOAD2` 协议：每 128 字节等待设备确认，避免 USB CDC 丢包。
- 上传完成后设备立即重新打开视频，并在 EOF 后自动循环。
- 视频列表保存在 Mac 上；选择播放时会把对应 AVI 重新上传到设备。

应用只访问用户填写的后端地址；不会把本机视频上传到其他云端。
