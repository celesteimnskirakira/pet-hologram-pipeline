"use client";

import { Check } from "lucide-react";
import { cn } from "@/utils/utils";
import type { GenerationStage } from "@/lib/api";

export interface StageItem {
  key: GenerationStage;
  label: string;
}

const ORDER: GenerationStage[] = [
  "queued",
  "validating",
  "generating_still",
  "generating_video",
  "post_processing",
  "delivering",
];

/**
 * Staged step list for the waiting screen. Only stages that have been reached
 * are shown — each new stage pops in as it becomes active, earlier ones stay as
 * completed steps with a lime check. The active step pulses gently.
 */
export function StageList({
  stages,
  current,
}: {
  stages: StageItem[];
  current: GenerationStage;
}) {
  const currentIndex =
    current === "completed" ? ORDER.length : ORDER.indexOf(current);

  // Reveal only reached stages (0..currentIndex), newest at the bottom.
  const visible = stages.filter((_, i) => i <= currentIndex);

  return (
    <ul className="flex flex-col gap-3" data-el="stage-list">
      {visible.map((s, i) => {
        const done = i < currentIndex;
        const active = i === currentIndex;
        return (
          <li
            key={s.key}
            className={cn(
              "flex items-center gap-3 text-[15px]",
              done && "opacity-70",
            )}
            style={{ animation: "petta-rise .45s ease both" }}
            data-el="stage-item"
          >
            <span
              className={cn(
                "grid h-6 w-6 shrink-0 place-items-center rounded-full border",
                done
                  ? "border-transparent bg-[var(--petta-lime)] text-[#1d170f]"
                  : "border-[var(--petta-line)] text-[var(--petta-cream)]",
              )}
              style={
                active
                  ? {
                      animation: "petta-pulse-dot 1.4s ease-in-out infinite",
                      background: "rgba(201,242,107,.18)",
                    }
                  : undefined
              }
            >
              {done ? (
                <Check className="h-3.5 w-3.5" strokeWidth={3} />
              ) : (
                <span
                  className="h-1.5 w-1.5 rounded-full bg-current"
                  style={
                    active
                      ? { animation: "petta-pulse-dot 1.2s ease-in-out infinite" }
                      : undefined
                  }
                />
              )}
            </span>
            <span
              className={cn(
                active ? "font-semibold text-white" : "text-[var(--petta-cream)]",
              )}
            >
              {s.label}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
