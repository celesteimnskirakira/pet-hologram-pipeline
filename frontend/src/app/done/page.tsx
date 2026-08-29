"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { AmbientShell } from "@/components/petta/ambient-shell";
import { flowStore, useFlow } from "@/stores/flow-store";

/** Small holographic base + acrylic panel glyph with a rising pet silhouette. */
function HologramGlyph() {
  return (
    <div className="relative grid place-items-center" style={{ width: 200, height: 200 }} aria-hidden>
      {/* Emanating cone of light */}
      <div
        className="absolute bottom-[34%] h-24 w-24 rounded-full"
        style={{
          background: "radial-gradient(circle, rgba(201,242,107,.4), transparent 70%)",
          animation: "petta-breathe 3.4s ease-in-out infinite",
        }}
      />
      {/* Rising pet silhouette */}
      <svg
        viewBox="0 0 48 48"
        className="absolute bottom-[38%] h-16 w-16 text-[var(--petta-lime)]"
        style={{ animation: "petta-floaty 4s ease-in-out infinite", filter: "drop-shadow(0 0 14px rgba(201,242,107,.6))" }}
      >
        <path d="M24 40c-7 0-13-4.5-13-12 0-6 4-11 13-11s13 5 13 11c0 7.5-6 12-13 12Z" fill="currentColor" opacity="0.92" />
        <ellipse cx="14" cy="14" rx="3.4" ry="5" fill="currentColor" />
        <ellipse cx="34" cy="14" rx="3.4" ry="5" fill="currentColor" />
      </svg>
      {/* Acrylic panel */}
      <div
        className="absolute bottom-[26%] h-16 w-24 rounded-t-lg border border-t-[var(--petta-line)] border-x-[var(--petta-line)]"
        style={{ background: "linear-gradient(180deg, rgba(237,231,224,.16), rgba(237,231,224,.04))", backdropFilter: "blur(6px)" }}
      />
      {/* Base */}
      <div
        className="absolute bottom-[18%] h-4 w-28 rounded-full border border-[var(--petta-line)]"
        style={{ background: "rgba(237,231,224,.2)", boxShadow: "0 10px 30px rgba(0,0,0,.4)" }}
      />
    </div>
  );
}

export default function DonePage() {
  const { t } = useTranslation();
  const router = useRouter();
  const flow = useFlow();

  useEffect(() => {
    // Guard against direct navigation without a completed task.
    if (!flow.taskId) router.replace("/");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function again() {
    flowStore.reset();
    router.replace("/upload");
  }

  return (
    <AmbientShell scatter={false}>
      <section
        className="flex flex-1 flex-col items-center justify-center gap-6 text-center"
        style={{ animation: "petta-rise .6s ease both" }}
        data-el="done-stage"
      >
        <HologramGlyph />
        <h2
          className="m-0 text-white"
          style={{ fontSize: "clamp(34px,11vw,52px)", lineHeight: 1.1, letterSpacing: "-0.04em", fontWeight: 600 }}
        >
          {t("petta.done.title")}
        </h2>
        <p className="mx-auto max-w-[18em] text-sm leading-relaxed text-[var(--petta-muted)]">
          {t("petta.done.sub")}
        </p>
      </section>

      <footer className="flex justify-center" data-el="done-actions">
        <button
          type="button"
          onClick={again}
          className="min-h-[52px] rounded-full bg-[var(--petta-lime)] px-8 text-base font-extrabold text-[#1d170f] transition-transform active:scale-[.98]"
          data-el="done-again"
        >
          {t("petta.done.again")}
        </button>
      </footer>
    </AmbientShell>
  );
}
