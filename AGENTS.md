# AGENTS.md

## 代码仓库

- GitHub 仓库：`git@github.com:celesteimnskirakira/pet-hologram-pipeline.git`
- 当前本地目录尚未配置 Git remote，工作树处于未提交状态；提交前确认远程仓库可见性、默认分支和访问凭据。
- 本地下载副本：`/Users/chen/Downloads/pet-hologram-pipeline-main`。该目录没有 `.git` 元数据，但包含当前较完整的应用原型。
- 不要在仓库、截图、提交记录或日志中保存服务器密码、SSH 私钥、API Key、设备密钥或第三方模型凭据。

## 目标产品链路（当前版本）

```text
用户扫码
  -> 进入 HTTPS 前端网站
  -> 用户上传宠物图片
  -> 后端创建任务并校验图片
  -> 后端调用生图 API，生成纯黑背景正视图
  -> 后端导入服务端预定的 video prompt + 黑底正视图
  -> 后端调用生视频模型生成视频
  -> 后端做循环、黑底、格式验收和必要后处理
  -> 后端通过 HTTPS 将成品下发到连接全息投影设备的电脑
  -> 投影电脑确认接收、校验并准备播放
  -> 后端向前端返回“已完成”
```

特征抽取、睡姿桥接帧和全息布局属于后端内部实现步骤，可以保留，但不能改变上述对用户可见的主链路。

### 动作与 prompt 规则

- 上传图片后，生图步骤固定使用 prompt：`生成图片中的宠物的完整全身正面图背景为纯黑色比例为一比一`。该 prompt 由后端维护，前端不能覆盖。
- 视频步骤只生成一个视频。后端从项目 prompt 库的四个动作中随机选择一个：`舔毛`、`走路`、`睡觉`、`挠脖子`。选中的完整 prompt 从 `petloop/action_prompts/` 读取，并与黑底正视图一起提交给视频模型。
- 每次任务必须记录选中的动作（例如 `selected_action` 或任务事件字段），便于前端展示和审计；不得默认并行生成四个视频。
- 四个动作 prompt 原文文件位于下载版仓库 `petloop/action_prompts/舔毛.txt`、`走路.txt`、`睡觉.txt`、`挠脖子.txt`。

### 当前模型服务商与密钥配置

- 生图服务商 Base URL：`https://api.openai-next.com`。
- 生图模型：`doubao-seedream-5-0-260128`。
- 生视频服务商：火山方舟官方 API，不经过第三方中转。当前确认模型 ID 为 `doubao-seedance-2-0-mini-260615`。
- 下载版仓库的密钥占位在 `.env.example`：`IMAGE_API_KEY=`、`VIDEO_API_KEY=`，真实值只填本机未提交的 `.env` 或密钥管理系统。
- 生图和生视频是两条独立 provider 通道；前端不能传入或看到任意 API key。
- 生图接口默认按 OpenAI-compatible `/v1` 前缀调用，可通过 `IMAGE_API_PREFIX` 调整。需以 `api.openai-next.com` 的实际文档确认为准。
- `doubao-seedream-5-0-260128` 只代表生图模型；特征抽取仍需视觉模型，待填写 `IMAGE_VISION_MODEL`，或另接 `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL`。
- `VIDEO_BASE_URL` 默认使用火山方舟官方地址 `https://ark.cn-beijing.volces.com/api/v3`，`VIDEO_MODEL` 默认值为 `doubao-seedance-2-0-mini-260615`。创建任务路径、首帧/尾帧字段、状态值和视频 URL 字段仍需按方舟官方 API 文档实测确认。

### 当前模型服务商配置

- **生图服务商**：`https://api.openai-next.com`。当前指定模型为 `doubao-seedream-5-0-260128`。
- **生视频服务商**：火山方舟官方 API，模型为 `doubao-seedance-2-0-mini-260615`。API key 使用方舟控制台生成的凭据。
- 两条通道必须在后端独立配置，不能再把生图和生视频绑定为同一个 `provider`。
- API key 只放在后端 `.env`、环境变量或密钥管理系统；前端、日志、报告和 Git 中不得出现真实值。
- 下载版仓库的占位位置已更新为：`IMAGE_API_KEY` 和 `VIDEO_API_KEY`；对应地址/模型为 `IMAGE_BASE_URL`、`IMAGE_MODEL`、`VIDEO_BASE_URL`、`VIDEO_MODEL`。
- 如生图服务商不支持视觉特征抽取，需要单独配置 `VISION_API_KEY`、`VISION_BASE_URL`、`VISION_MODEL`；未确认前不得假设 `doubao-seedream-5-0-260128` 可以完成视觉理解。
- 方舟视频请求路径、首帧/尾帧字段和轮询响应字段仍需按官方文档实测确认。

### 各环节职责

1. **扫码与前端入口**：二维码只指向 HTTPS 域名；前端展示上传页、任务状态、错误和完成结果，不暴露模型 API key。
2. **图片上传**：后端接收 multipart 图片，限制大小和格式，做真实图片解析、EXIF 方向校正、尺寸规范化，并生成 `job_id`。
3. **黑底正视图**：后端使用服务端固定的生图模型和 prompt，结合上传原图及内部特征约束生成黑底正视图；黑底检测不达标时自动重试。前端不能直接改 prompt 或模型。
4. **视频生成**：后端使用服务端预定的 video prompt，将黑底正视图作为输入帧调用视频模型；模型、时长、分辨率、参数和成本上限由后端配置。
5. **后处理与验收**：后端检查视频可播放性、时长、分辨率、编码和黑边是否符合投影端协议，并执行接缝修整、运动检测及单面/四面全息布局渲染。
6. **HTTPS 下发**：后端只向已登记的投影电脑或投影端接收服务发送成品，使用 HTTPS、设备认证和一次性或短时有效下载凭证；不能把服务器文件目录直接暴露给客户端。投影电脑必须返回接收确认、文件校验结果和播放准备状态。
7. **完成回传**：只有投影电脑确认文件已接收并通过校验后，任务才进入 `completed`，前端才显示“已完成”。生成 API 返回视频地址不能单独视为完成。

### 步骤完成回传契约

用户上传后，后端每完成一个阶段都必须更新任务并通过后续接口向前端回传，不允许前端解析日志文本猜测进度。至少包含：

- `job_id`：任务唯一标识。
- `status`：当前统一状态。
- `stage`：当前阶段名称。
- `progress`：阶段进度或百分比；无法精确估计时允许为 `null`。
- `error_code`：机器可读错误码，成功时为 `null`。
- `message`：用户可理解的状态或错误信息。
- `artifacts`：已完成阶段可公开的产物引用；使用受控 HTTPS URL，不返回本地文件路径。
- `updated_at`：服务端时间戳。

前端后续接口可采用轮询或 SSE/WebSocket，但必须消费同一套状态模型。每次阶段完成至少产生一次事件：`image_validated`、`still_completed`、`video_completed`、`post_processing_completed`、`delivery_completed`。

### 统一任务状态

主流程：`queued` → `validating` → `generating_still` → `generating_video` → `post_processing` → `delivering` → `completed`

异常状态：`failed`、`delivery_failed`、`expired`、`cancelled`。每个任务记录当前阶段、进度、错误码、用户可见错误信息、产物引用和时间戳。

## 当前实现对照

- 下载版仓库已覆盖上传、特征抽取、生图、睡姿桥接、生视频、循环检测、全息渲染和网页状态展示的大部分**生成链路**。
- `server.py` / `display.py` 当前主要是本机或局域网演示服务，投影页面通过浏览器读取本地生成文件。尚未实现后端向独立投影电脑进行 HTTPS 下发并等待确认的正式交付链路。
- 当前任务状态主要在进程内存，尚无持久化 job、投影设备注册、设备心跳、HTTPS 双向认证、下发重试、幂等键和接收确认协议。
- 本机单用户和封闭局域网原型可以联调，但不能把生成完成或视频 URL 当作最终产品完成。

## 下载版仓库当前进度（2026-08-29）

已实现：

1. 宠物照片上传、EXIF 方向校正、格式/尺寸/文件大小校验和规范化。
2. 视觉模型抽取结构化宠物特征。
3. 图像模型生成纯黑背景正视图，支持黑底评分、自动重试和确定性压黑。
4. 睡姿桥接帧生成。
5. Ark Seedance 或 Agnes 视频模型适配器，生成 5 秒首尾循环睡眠视频。
6. ffmpeg 接缝度量、裁尾或 xfade、运动检测、GIF 预览和视频规格探测。
7. 单动作与多动作路演模式、任务提交限流和 429 重试。
8. 单面 45° 反射及四面锥形 `quad` 全息渲染，包含镜像、黑位压制、边缘黑度检测和播放清单。
9. `server.py` 本机上传网页；`display.py` 展台操作台、访客扫码页和 `/stage` 投影页。
10. `cli.py` 的 `doctor`、`price`、`run`、`still`、`loop`、`seam` 命令。

代码组织：

- `petloop/config.py`：模型、提供商、视频、全息和运行参数。
- `petloop/providers.py`：Ark/Agnes HTTP API、轮询、TLS/429/重试和提交限流。
- `petloop/pipeline.py`：主编排、单动作/多动作流程、报告和播放清单。
- `petloop/imaging.py`：图片规范化、黑底评分和清理。
- `petloop/looping.py`：接缝、运动、GIF、单面/四面全息渲染和边缘检测。
- `petloop/prompts.py`：特征抽取、正视图、睡姿桥接和循环视频 prompt。
- `petloop/pricing.py`：Seedance 价格、促销和 token 成本估算。
- `petloop/diagnostics.py`：API key、代理、端点和 ffmpeg 自检。
- `tests/test_pipeline.py`：本地确定性测试。

README 声称已完成真实 API 全链路、Agnes 备用通道、多动作路演流程和 66 个测试全绿；本机复核曾因缺少 `Pillow` 在测试导入阶段失败，历史声明仍需在当前环境独立重现。

## 前后端联调与服务化要求

当前接口可用于原型：

- `server.py`：`GET /api/doctor`、`GET /api/price`、`POST /api/run`、`GET /api/job/{id}`、`GET /api/file`。
- `display.py`：`POST /api/enqueue`、`GET /api/my/{id}?k=...`、`GET /api/jobs`、`GET/POST /api/stage`、`GET /api/qr.svg`、`GET /api/file`。

接独立前端和正式投影端前必须完成：

1. 统一并版本化 `/api/v1` 契约，统一状态、事件、错误和受控产物 URL。
2. 为每个阶段实现状态持久化和完成事件接口；支持轮询或 SSE，保证重试不重复推进状态。
3. 将内存线程迁移到数据库 + Redis/队列 + worker，支持重启恢复、超时、取消、重试、幂等键和并发上限。
4. 实现投影设备注册、心跳、设备认证、HTTPS 下发、校验确认、播放准备回执和下发重试。
5. 增加用户、操作台、投影端、管理接口的权限分层；公网必须 HTTPS。
6. 增加上传配额、真实 MIME/图片内容校验、速率限制、对象存储、短时下载凭证和自动清理。
7. 服务端固定 provider、模型、prompt、分辨率、rig 和成本上限，不能由客户端任意覆盖。
8. 补 API 集成测试和端到端测试，覆盖逐步回传、失败状态、重复提交、文件下载、权限、断点恢复和投影确认。
9. 增加正式部署配置：Nginx、HTTPS、systemd、结构化日志、监控和日志轮转。

建议顺序：先用现有页面验证生成链路；然后定义 `/api/v1/jobs` 及阶段事件接口并接入新前端；再实现投影电脑接收服务和确认协议；最后引入持久化队列、对象存储、鉴权和生产部署。

## 云服务器与部署约束

- 实例 ID：`i-6a92976c53e6c5f5002a6398`
- 区域：中国（常山 2）
- 规格：轻量型 T1S，`ecs.t1s.c2m2`，2 核 CPU、2 GiB 内存、20 GiB SSD
- 操作系统：Ubuntu 24.04 LTS
- 内网 IPv4：`10.222.14.225`
- SSH 内网端口：`22`
- 公网 IP 可能变化，正式访问必须使用域名和 HTTPS，部署脚本不得硬编码公网地址。

服务器适合运行 FastAPI/Uvicorn、任务状态服务、FFmpeg 后处理和 Nginx 反向代理；不应默认运行 GPU 或高内存视频生成模型。视频模型走外部 API 或独立 GPU 服务。

2 核 CPU、2 GiB 内存下，视频转换默认单任务串行，并设置超时、失败重试和临时文件清理。生产流程优先使用对象存储，服务器仅保留临时文件。

端口规划：`22` SSH、`80` HTTP、`443` HTTPS、`8000` FastAPI 临时测试端口。新增端口前检查云安全组、端口映射和 UFW。

## 前端 `petta-holo-pet` 接口契约（已核对）

前端代码位于 `/Users/chen/Downloads/petta-holo-pet`，是 Next.js 16 App Router + React 19 + TypeScript + Bun 应用。以下接口说明来自当前源码（不是目标产品的未来设计），后端改造时须保持兼容或同步修改前端 typed helper。

### 用户侧生成流程

前端实际调用顺序为：

```text
选择图片
  -> storage.upload(path, file) 直传 Eazo 对象存储
  -> POST /api/generation 创建任务
  -> 每约 700ms GET /api/generation/{taskId} 轮询
  -> status=success 后进入完成页并读取 videoUrl
```

调用方集中在 `petta-holo-pet/src/lib/api/generation.ts`，页面通过 `@/lib/api` 导入，禁止在组件内新增裸 `fetch` 或重复拼接接口字段。

#### 1. 图片上传（Eazo Storage）

- SDK：`storage.upload(path, file)`，不是本项目的 HTTP 路由。
- 路径格式：`pet-photos/{Date.now()}-{safeName}`；文件名仅保留 ASCII 字母、数字、`.`、`_`、`-`，空文件名回退为 `photo`。
- 返回：`{ key, url }`，前端映射为 `{ imageId: key, imageUrl: url }`。
- 浏览器不会把图片字节转发到本项目后端；后端必须能访问并校验 `imageId`/`imageUrl` 指向的对象。
- 对象存储上传失败直接中断生成流程；当前 helper 未做自定义 MIME/大小校验，生产约束应在服务端补齐。

#### 2. 创建任务

`POST /api/generation`

请求头由 `src/lib/api/request.ts` 自动注入：`x-eazo-session`（存在时）、`x-app-locale`（`en-US` 或 `zh-CN`）和 `content-type: application/json`。

请求 JSON：`{ "imageId": "<object key>", "imageUrl": "<object URL>" }`。两个字段均为可选字符串；缺失或非字符串值按 `null` 处理，当前不会因空输入返回 400。成功响应为 `{ "taskId": "task_<opaque id>" }`。

当前实现创建数据库记录时使用 `status="processing"`、`stage="uploading"`、`progress=0`。任务表为 `generation_tasks`，字段包括 `imageId`、`imageUrl`、`status`、`stage`、`progress`、`videoUrl`、`error`、`startedAt`、`updatedAt`。

#### 3. 查询任务

`GET /api/generation/{taskId}`

成功响应：`{ taskId, status, stage, progress, videoUrl? }`，其中当前状态为 `pending | processing | success | failed`，阶段为 `queued | uploading | creating_task | generating_video | sending_hardware | success | failed`。任务不存在返回 HTTP `404` 和 `{ "error": "not_found" }`；其它非 2xx 响应由 helper 转为 `poll_generation_failed`。前端生成页按约 700ms 间隔轮询，`success` 或 `failed` 时停止。

#### 4. 外部生成后端回写

`PATCH /api/generation/{taskId}` 预留给外部生成/硬件后端，不是浏览器调用接口。请求 JSON 可包含 `status`、`stage`、`progress`、`videoUrl`、`error`。无效 JSON 返回 `400/invalid_body`；任务不存在返回 `404/not_found`；成功返回 `{ ok: true, taskId, status }`。

此回写路由当前没有独立设备认证、签名、幂等键或权限校验，公网部署前必须置于内部网络或补充服务间认证，并严格校验统一状态、阶段和错误码。

### 前端类型与目标协议的差异

- 前端使用 `pending | processing | success | failed`；目标协议使用 `queued/validating/generating_still/generating_video/post_processing/delivering/completed` 等状态，接入正式 `/api/v1` 前需做明确映射。
- 当前响应缺少目标协议要求的 `job_id`、`error_code`、用户消息、`artifacts`、`updated_at` 和阶段事件。
- 当前轮询约 14 秒后会自动写入 `success`，没有真实产物时生成 `petta://clip/{taskId}` 占位 URL；不能作为真实视频生成或投影交付完成条件。

### API 客户端与认证约束

- 所有浏览器 API 请求使用 `src/lib/api/request.ts`，调用 `auth.getSessionHeader()` 并添加会话与 locale 请求头。
- `appAIRequest` 仅对 HTTP 402 且错误码为 `app_ai_unavailable` 的响应显示统一提示并抛出错误；其它响应保持原样。
- `EAZO_PRIVATE_KEY`、AI/provider 凭据和数据库连接串只能存在服务端环境变量或密钥管理系统，不能进入前端 bundle、`NEXT_PUBLIC_*`、日志或接口响应。

### 前端运行与验证

在 `/Users/chen/Downloads/petta-holo-pet` 执行：`bun install`、`bun run lint`、`bun run build`、`bun run dev`。数据库命令为 `bun run db:generate`、`bun run db:migrate`、`bun run db:push`。必需环境变量至少包括 `EAZO_APP_ID`、`EAZO_PRIVATE_KEY`、`DATABASE_URL`、`NEXT_PUBLIC_APP_TITLE`、`NEXT_PUBLIC_APP_DESCRIPTION`；未配置 `DATABASE_URL` 时回退本机 PostgreSQL 默认连接串，仅适合开发环境。
