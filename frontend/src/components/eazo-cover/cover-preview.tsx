"use client";

import { useEffect, useState } from "react";
import { PettaOrbLoader } from "@/components/petta/orb-loader";

/** Deterministic, privacy-safe preview data. No user data, no network. */
const COVER_PREVIEW_DATA = {
  stages: ["生成动作视频", "下发到硬件", "请看硬件"],
};

/**
 * Autonomous ~4.5s loop that demonstrates PETTA's signature payoff:
 * a photo generating through a breathing orb, reaching 100%, then the
 * "look at the device" hologram reveal — then it loops. No auth, no store,
 * no network; purely local state.
 */
export function CoverPreview() {
  const [progress, setProgress] = useState(6);
  const [phase, setPhase] = useState<"generating" | "done">("generating");

  useEffect(() => {
    let raf = 0;
    let start = performance.now();
    const GEN_MS = 3200;
    const HOLD_MS = 1500;

    function frame(now: number) {
      const elapsed = now - start;
      if (elapsed < GEN_MS) {
        const raw = elapsed / GEN_MS;
        setProgress(Math.min(100, Math.round(6 + raw * 94)));
        setPhase("generating");
      } else if (elapsed < GEN_MS + HOLD_MS) {
        setProgress(100);
        setPhase("done");
      } else {
        start = now;
        setProgress(6);
        setPhase("generating");
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  const label =
    phase === "done"
      ? COVER_PREVIEW_DATA.stages[2]
      : progress >= 78
        ? COVER_PREVIEW_DATA.stages[1]
        : COVER_PREVIEW_DATA.stages[0];

  return (
    <div
      className="relative isolate grid min-h-[100svh] w-full place-items-center overflow-hidden"
      data-el="cover-preview"
    >
      <div className="petta-ambient" aria-hidden />
      <div className="petta-grain" aria-hidden />

      <div className="flex flex-col items-center gap-7 px-6 text-center">
        {phase === "done" ? (
          <DoneReveal />
        ) : (
          <PettaOrbLoader size={230} active />
        )}

        <div className="space-y-1">
          <h2
            className="m-0 text-white"
            style={{ fontSize: "28px", letterSpacing: "-0.03em", fontWeight: 600 }}
          >
            {phase === "done" ? "petta." : "正在把它带到你身边"}
          </h2>
          <p className="text-sm text-[var(--petta-muted)]">{label}</p>
        </div>

        {phase === "generating" && (
          <div className="w-[260px]">
            <div className="mb-2 flex justify-between text-xs text-[var(--petta-muted)]">
              <span>{label}</span>
              <span className="tabular-nums text-[var(--petta-cream)]">{progress}%</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[rgba(237,231,224,.16)]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${progress}%`,
                  background: "linear-gradient(90deg,var(--petta-lime),var(--petta-cream))",
                }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DoneReveal() {
  return (
    <div
      className="relative grid place-items-center"
      style={{ width: 230, height: 230, animation: "petta-rise .5s ease both" }}
      aria-hidden
    >
      <div
        className="absolute bottom-[38%] h-28 w-28 rounded-full"
        style={{
          background: "radial-gradient(circle, rgba(201,242,107,.42), transparent 70%)",
          animation: "petta-breathe 3s ease-in-out infinite",
        }}
      />
      <svg
        viewBox="0 0 48 48"
        className="absolute bottom-[42%] h-20 w-20 text-[var(--petta-lime)]"
        style={{ animation: "petta-floaty 3.6s ease-in-out infinite", filter: "drop-shadow(0 0 16px rgba(201,242,107,.6))" }}
      >
        <path d="M24 40c-7 0-13-4.5-13-12 0-6 4-11 13-11s13 5 13 11c0 7.5-6 12-13 12Z" fill="currentColor" opacity="0.92" />
        <ellipse cx="14" cy="14" rx="3.4" ry="5" fill="currentColor" />
        <ellipse cx="34" cy="14" rx="3.4" ry="5" fill="currentColor" />
      </svg>
      <div
        className="absolute bottom-[28%] h-20 w-28 rounded-t-lg border border-[var(--petta-line)]"
        style={{ background: "linear-gradient(180deg, rgba(237,231,224,.16), rgba(237,231,224,.04))", backdropFilter: "blur(6px)" }}
      />
      <div
        className="absolute bottom-[20%] h-4 w-32 rounded-full border border-[var(--petta-line)]"
        style={{ background: "rgba(237,231,224,.2)", boxShadow: "0 10px 30px rgba(0,0,0,.4)" }}
      />
    </div>
  );
}
