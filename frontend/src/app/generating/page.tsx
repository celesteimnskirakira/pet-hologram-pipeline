"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { AmbientShell } from "@/components/petta/ambient-shell";
import { PettaOrbLoader } from "@/components/petta/orb-loader";
import { StageList, type StageItem } from "@/components/petta/stage-list";
import { getGeneration, type GenerationStage } from "@/lib/api";
import { flowStore, useFlow } from "@/stores/flow-store";
export default function GeneratingPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const flow = useFlow();
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState<GenerationStage>("queued");
  const [failed, setFailed] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stages: StageItem[] = [
    { key: "queued", label: "已进入队列" },
    { key: "validating", label: t("petta.generating.stageUploading") },
    { key: "generating_still", label: t("petta.generating.stageCreatingTask") },
    { key: "generating_video", label: t("petta.generating.stageGeneratingVideo") },
    { key: "post_processing", label: "正在验收视频" },
    { key: "delivering", label: t("petta.generating.stageSendingHardware") },
  ];

  useEffect(() => {
    // No active task (e.g. direct navigation) — send back to upload.
    if (!flow.taskId) {
      router.replace("/upload");
      return;
    }
    let cancelled = false;
    const taskId = flow.taskId;

    async function poll() {
      try {
        const state = await getGeneration(taskId);
        if (cancelled) return;
        setProgress(state.progress);
        setStage(state.stage);
        flowStore.setCompletion({
          selectedAction: state.selectedAction,
          videoUrl: state.videoUrl,
          displayCode: state.displayCode,
        });
        if (state.status === "completed" || state.status === "success") {
          setTimeout(() => !cancelled && router.replace("/done"), 700);
          return;
        }
        if (["failed", "delivery_failed", "expired", "cancelled"].includes(state.status)) {
          setFailed(true);
          return;
        }
        timerRef.current = setTimeout(poll, 700);
      } catch {
        if (!cancelled) setFailed(true);
      }
    }
    poll();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flow.taskId]);

  function retry() {
    router.replace("/upload");
  }

  if (failed) {
    return (
      <AmbientShell scatter={false}>
        <section className="flex flex-1 flex-col items-center justify-center gap-4 text-center">
          <h2 className="text-2xl font-semibold text-white">
            {t("petta.generating.errorTitle")}
          </h2>
          <p className="max-w-[18em] text-sm text-[var(--petta-muted)]">
            {t("petta.generating.errorSub")}
          </p>
          <button
            type="button"
            onClick={retry}
            className="mt-2 min-h-[48px] rounded-full bg-[var(--petta-cream)] px-8 font-bold text-[#23170f]"
            data-el="generating-retry"
          >
            {t("petta.generating.retry")}
          </button>
        </section>
      </AmbientShell>
    );
  }

  return (
    <AmbientShell scatter={false}>
      <section
        className="flex flex-1 flex-col items-center justify-center gap-8 text-center"
        data-el="generating-stage"
        aria-live="polite"
      >
        <PettaOrbLoader size={250} photoUrl={flow.photoUrl} active />

        <div className="space-y-2">
          <h2
            className="m-0 text-white"
            style={{ fontSize: "clamp(24px,7.5vw,32px)", letterSpacing: "-0.03em", fontWeight: 560 }}
          >
            {t("petta.generating.title")}
          </h2>
          <p className="mx-auto max-w-[18em] text-sm leading-relaxed text-[var(--petta-muted)]">
            {t("petta.generating.sub")}
          </p>
          {flow.displayCode && (
            <p className="text-sm font-semibold text-[var(--petta-cream)]">展示码：{flow.displayCode}</p>
          )}
        </div>

        {/* Breathing progress */}
        <div className="w-full max-w-[300px]">
          <div className="mb-2 flex items-center justify-between text-xs text-[var(--petta-muted)]">
            <span>{stages.find((s) => s.key === stage)?.label ?? ""}</span>
            <span className="tabular-nums text-[var(--petta-cream)]">
              {t("petta.generating.percent", { value: progress })}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(237,231,224,.16)]">
            <div
              className="h-full rounded-full transition-[width] duration-500 ease-out"
              style={{
                width: `${progress}%`,
                background: "linear-gradient(90deg,var(--petta-lime),var(--petta-cream))",
              }}
            />
          </div>
        </div>
      </section>

      <footer className="pt-2" data-el="generating-steps">
        <div
          className="rounded-[24px] border border-[var(--petta-line)] p-5"
          style={{ background: "rgba(32,24,18,.5)", backdropFilter: "blur(18px)", WebkitBackdropFilter: "blur(18px)" }}
        >
          <StageList stages={stages} current={stage} />
        </div>
      </footer>
    </AmbientShell>
  );
}
