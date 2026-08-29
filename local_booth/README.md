# 本地全息体验台（独立入口）

这是一个与 `Holo Video Uploader.app` 完全分开的现场体验方案：

```text
手机扫码上传宠物照片
        ↓
Mac 本地排队和保存任务
        ↓
调用已配置的图像/视频模型生成黑底动态 MP4
        ↓
Mac 本地合成四面 360×360 / 10 FPS / MJPEG AVI
        ↓
通过 USB UPLOAD2 推送到微雪并自动循环播放
```

USB 传输如果遇到分块超时，体验台会自动清理残留串口数据并重新发送，最多尝试 4 次。重传直接复用本地 AVI，不会再次调用云端生成模型或产生重复生成费用。

Mac 负责网页、队列、文件和 USB，因而不需要部署云服务器、域名、数据库或开放公网端口。生成模型本身仍需联网调用配置的 Ark/Agnes API。

## 与原上传软件的隔离

- 不启动、不导入、不修改 `Holo Video Uploader.app`。
- 不读写原上传软件的视频库和设置。
- 本方案的数据只写在 `local_booth/runs/`。
- 本方案默认使用 `8793` 端口，原服务端口不受影响。
- USB 同一时刻只能由一个程序占用；运行本地体验台时请关闭原上传 App，停止体验台后可照常打开原 App。

## 第一次配置

1. 下载并解压仓库。
2. 将 `.env.example` 复制为 `.env`，填入 `ARK_API_KEY` 或 `AGNES_API_KEY`。
3. 确认 Mac 已安装 FFmpeg（Homebrew：`brew install ffmpeg`）。如果仓库旁边已有 `tools/bin/ffmpeg`，启动脚本会自动使用它，无需重复安装。
4. 双击 `local_booth/setup_local_booth.command`。

## 每次现场启动

1. USB 连接微雪 ESP32-S3，并确认设备已运行支持 `UPLOAD2` 的固件。
2. 不要同时运行 `Holo Video Uploader.app`，避免两个程序争用同一个串口。
3. 双击 `local_booth/Local Holo Booth.app`；也可以双击 `start_local_booth.command`。
4. 浏览器会打开控制台并显示二维码；手机和 Mac 连接同一个 Wi-Fi 后即可扫码。
5. 关闭启动脚本的终端窗口即可停止服务。

`Local Holo Booth.app` 只是新体验台的独立启动器，不包含、替换或启动原来的 `Holo Video Uploader.app`。

## 命令行测试

仅测试已存在 MP4 的四面转换，不上传：

```bash
local_booth/.venv/bin/python local_booth/device_bridge.py input.mp4 \
  --output /tmp/current.avi --convert-only
```

转换并真实推送到微雪：

```bash
local_booth/.venv/bin/python local_booth/device_bridge.py input.mp4
```

只验证网页和生成流程、跳过 USB：

```bash
local_booth/.venv/bin/python local_booth/server.py --no-device
```

固定串口或二维码 IP：

```bash
local_booth/.venv/bin/python local_booth/server.py \
  --serial-port /dev/cu.usbmodem3101 --advertise 192.168.1.23
```
