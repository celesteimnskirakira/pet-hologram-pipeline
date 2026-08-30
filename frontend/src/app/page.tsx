"use client";

import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { AmbientShell } from "@/components/petta/ambient-shell";
import { LangToggle } from "@/components/petta/lang-toggle";

export default function HomePage() {
  const { t } = useTranslation();
  const router = useRouter();

  return (
    <AmbientShell>
      <LangToggle className="absolute right-5 top-[max(60px,calc(env(safe-area-inset-top,0px)+8px))] z-10" />

      <section
        className="flex flex-1 flex-col justify-center pb-[3vh] pt-[8vh]"
        data-el="home-stage"
        aria-label="PETTA"
      >
        <h1
          className="m-0 mb-4 select-none text-white"
          style={{
            fontSize: "clamp(50px, 18vw, 86px)",
            lineHeight: 0.92,
            letterSpacing: "-0.08em",
            fontWeight: 500,
            textTransform: "lowercase",
            textShadow: "0 18px 70px rgba(255,138,91,.28)",
          }}
        >
          petta<span className="text-[var(--petta-cream)] opacity-90">.</span>
        </h1>
        <p
          className="m-0 max-w-[11.5em] text-white/95"
          style={{
            fontSize: "clamp(24px, 8vw, 40px)",
            lineHeight: 1.08,
            letterSpacing: "-0.04em",
            fontWeight: 520,
          }}
        >
          {t("petta.home.copy")}
        </p>
        <p className="mt-3.5 max-w-[22em] text-sm leading-relaxed text-[var(--petta-muted)]">
          {t("petta.home.sub")}
        </p>
      </section>

      <footer className="flex items-center justify-center" data-el="home-actions">
        <button
          type="button"
          onClick={() => router.push("/upload")}
          className="grid h-[82px] w-[82px] place-items-center rounded-full border border-[var(--petta-line)] text-xs font-bold uppercase tracking-[0.12em] text-white transition-transform active:scale-95"
          style={{
            background: "rgba(237,231,224,.16)",
            backdropFilter: "blur(18px)",
            WebkitBackdropFilter: "blur(18px)",
            boxShadow:
              "var(--petta-shadow), inset 0 0 22px rgba(255,255,255,.08)",
          }}
          data-el="home-start"
        >
          {t("petta.home.start")}
        </button>
      </footer>
    </AmbientShell>
  );
}
