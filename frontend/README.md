A minimal Next.js starter for building apps inside the [Eazo](https://eazo.ai) platform. Includes a working example of the Eazo session token flow: the app requests the encrypted user token from the host via `postMessage`, sends it to a Next.js API route, decrypts it server-side with `@eazo/node-sdk`, and returns the user profile.

## Getting Started

Install dependencies with Bun:

```bash
bun install
```

If dependency installation stalls on this machine during `sharp` setup, use:

```bash
SHARP_IGNORE_GLOBAL_LIBVIPS=1 bun install
```

Then start the development server:

```bash
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

## 展会扫码入口

展台电脑打开 `http://<局域网IP>:3000/operator`，页面会显示用户扫码二维码和按展示码分组的任务队列。手机扫描后进入 `/upload`，提交后在生成页和完成页看到同一个展示码。局域网联调时将前端绑定到所有网卡：

```bash
npm run dev -- --hostname 0.0.0.0 --port 3000
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your private key:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `EAZO_PRIVATE_KEY` | Your Eazo developer private key (hex, 64 chars). Used server-side to decrypt the user session token. |

You can generate a keypair in the Eazo developer settings. Never expose the private key to the browser.

## Learn More

- [Eazo Documentation](https://docs.eazo.ai)
- [Next.js Documentation](https://nextjs.org/docs)

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
