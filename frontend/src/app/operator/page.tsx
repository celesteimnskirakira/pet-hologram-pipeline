"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import QRCode from "qrcode";

type Job = { taskId: string; displayCode: string | null; status: string; stage: string; progress: number; selectedAction: string | null; deliveryStatus: string | null; updatedAt: string };

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  processing: "处理中",
  completed: "已完成",
  success: "已完成",
  failed: "生成失败",
  delivery_failed: "下发失败",
  expired: "已过期",
  cancelled: "已取消",
};
const STAGE_LABEL: Record<string, string> = {
  queued: "排队中",
  validating: "校验图片",
  generating_still: "生成黑底图",
  generating_video: "生成视频",
  post_processing: "后处理验收",
  delivering: "发送到投影",
  completed: "已完成",
  failed: "生成失败",
  delivery_failed: "下发失败",
  expired: "已过期",
  cancelled: "已取消",
};
const ACTION_LABEL: Record<string, string> = {
  舔毛: "舔毛",
  走路: "走路",
  睡觉: "睡觉",
  挠脖子: "挠脖子",
};
const DELIVERY_LABEL: Record<string, string> = {
  ready: "已接收",
  received: "已接收",
  pending: "待发送",
};

export default function OperatorPage() {
  const [qr, setQr] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    const origin = window.location.origin;
    const nextUploadUrl = `${origin}/upload`;
    QRCode.toDataURL(nextUploadUrl, { width: 280, margin: 2 }).then(setQr);
    const poll = async () => {
      try {
        const response = await fetch("/api/operator/jobs", { cache: "no-store" });
        if (response.ok) setJobs(((await response.json()) as { jobs: Job[] }).jobs);
      } catch {
        // The operator view remains usable if the queue is temporarily unavailable.
      }
    };
    poll();
    const timer = window.setInterval(poll, 2000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <main style={{ minHeight: "100vh", background: "#0f1113", color: "#e7eaec", padding: 32, fontFamily: "system-ui, sans-serif" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", display: "grid", gap: 28, gridTemplateColumns: "320px 1fr" }}>
        <section style={{ background: "#171a1d", padding: 24, borderRadius: 8, textAlign: "center" }}>
          <h1 style={{ marginTop: 0, fontSize: 22 }}>展会扫码入口</h1>
          <p style={{ color: "#949ca3", fontSize: 14 }}>用户扫描二维码进入宠物上传页面</p>
          {qr ? <Image src={qr} alt="用户上传二维码" width={280} height={280} unoptimized style={{ background: "white", padding: 12 }} /> : <div style={{ height: 280 }} />}
          <p suppressHydrationWarning style={{ color: "#949ca3", fontSize: 12, wordBreak: "break-all" }}>{typeof window === "undefined" ? "" : `${window.location.origin}/upload`}</p>
        </section>
        <section style={{ background: "#171a1d", padding: 24, borderRadius: 8 }}>
          <h2 style={{ marginTop: 0, fontSize: 22 }}>任务队列</h2>
          <p style={{ color: "#949ca3", fontSize: 14 }}>用户用展示码确认自己的任务，操作台可据此识别内容。</p>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead><tr style={{ color: "#949ca3", textAlign: "left" }}><th>展示码</th><th>动作</th><th>阶段</th><th>进度</th><th>投影</th></tr></thead>
              <tbody>{jobs.map((job) => <tr key={job.taskId} style={{ borderTop: "1px solid #2b3136" }}><td style={{ padding: "12px 8px", fontWeight: 700 }}>{job.displayCode ?? job.taskId.slice(-6).toUpperCase()}</td><td>{ACTION_LABEL[job.selectedAction ?? ""] ?? (job.status === "completed" || job.status === "success" ? "已生成" : "随机选择中")}</td><td>{STAGE_LABEL[job.stage] ?? STATUS_LABEL[job.status] ?? job.stage}</td><td>{job.progress}%</td><td>{DELIVERY_LABEL[job.deliveryStatus ?? ""] ?? (job.status === "failed" ? "未发送" : job.status === "delivery_failed" ? "下发失败" : "待发送")}</td></tr>)}</tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  );
}
