"use client";

import { cn } from "@/utils/utils";

/**
 * Shared ambient shell — the warm soft-focus depth ground + grain + floating
 * scatter used by every screen. Wraps the page content in the 460px column.
 */
export function AmbientShell({
  children,
  scatter = true,
  className,
}: {
  children: React.ReactNode;
  scatter?: boolean;
  className?: string;
}) {
  return (
    <div
      className="relative isolate grid min-h-[100svh] w-full place-items-stretch justify-items-center overflow-hidden"
      style={{
        paddingTop: "max(56px, env(safe-area-inset-top, 0px))",
        paddingBottom: "max(34px, env(safe-area-inset-bottom, 0px))",
      }}
      data-el="ambient-shell"
    >
      <div className="petta-ambient" aria-hidden />
      <div className="petta-grain" aria-hidden />
      {scatter && (
        <div className="pointer-events-none absolute inset-0" aria-hidden>
          <Orb className="right-[8%] top-[12%] h-14 w-14" delay="0s" />
          <Orb
            className="right-[16%] bottom-[16%] h-6 w-6 !bg-[var(--petta-lime)] opacity-90"
            delay="-2s"
          />
          <Arc className="right-[7%] top-[66%] rotate-[150deg]" />
          <Arc className="left-[6%] bottom-[66%] rotate-[-28deg]" />
        </div>
      )}
      <main
        className={cn(
          "flex w-full max-w-[460px] min-w-0 flex-col px-5 pb-5 pt-4",
          className,
        )}
        style={{
          minHeight:
            "calc(100svh - max(56px, env(safe-area-inset-top,0px)) - max(34px, env(safe-area-inset-bottom,0px)))",
        }}
      >
        {children}
      </main>
    </div>
  );
}

function Orb({ className, delay }: { className?: string; delay: string }) {
  return (
    <span
      className={cn(
        "absolute rounded-full border border-[var(--petta-line)] bg-[var(--petta-glass)] backdrop-blur-md",
        className,
      )}
      style={{
        boxShadow:
          "inset 0 0 18px rgba(255,255,255,.08), 0 18px 50px rgba(0,0,0,.18)",
        animation: `petta-floaty 7s ease-in-out infinite`,
        animationDelay: delay,
      }}
    />
  );
}

function Arc({ className }: { className?: string }) {
  return (
    <span
      className={cn("absolute h-[38px] w-[74px] opacity-90", className)}
      style={{
        border: "7px solid var(--petta-lime)",
        borderLeft: 0,
        borderBottom: 0,
        borderRadius: "0 48px 0 0",
      }}
    />
  );
}
