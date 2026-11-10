# Petta 全息桌宠前端

独立运行的 Next.js 前端，不依赖 Eazo。正式站点为
`https://app.genpichong.dpdns.org`，通过 Cloudflare Tunnel 转发到服务器本机
`127.0.0.1:3000`。

## 用户流程

1. 用户扫码进入 `/upload` 并上传宠物图片。
2. 服务端把图片保存到受控目录，并返回带 HMAC 签名和有效期的 HTTPS URL。
3. `POST /api/generation` 创建 PostgreSQL 任务，并向 Python 后端
   `POST /api/v1/jobs`。
4. 页面轮询本地数据库；Python 后端每完成一个阶段，通过鉴权回调更新任务。
5. 只有投影接收端完成下载、SHA-256 校验并返回 ready 后，页面才显示完成。

## 本地验证

```bash
npm install
npm run lint
npm run build
npm run dev
```

复制 `.env.example` 为 `.env`，仅填写本地或测试值。所有共享密钥只能放在服务端环境变量中，不能使用 `NEXT_PUBLIC_` 前缀。

## 生产部署

- Next.js standalone 产物由 `petta-frontend.service` 管理。
- PostgreSQL 只监听本机地址。
- 前端只监听 `127.0.0.1:3000`，不开放公网端口。
- Cloudflare Tunnel 公网路由：
  `app.genpichong.dpdns.org -> http://127.0.0.1:3000`。
- Python 后端保持：
  `genpichong.dpdns.org -> http://127.0.0.1:8000`。

部署模板见 `deploy/petta-frontend.service` 和 `deploy/frontend.env.example`。
