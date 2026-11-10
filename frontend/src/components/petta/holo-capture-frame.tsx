"use client";

import { cn } from "@/utils/utils";
import { PetFaceMorph } from "@/components/petta/pet-face-morph";

/**
 * HoloCaptureFrame — a holographic viewfinder upload zone that replaces the
 * generic dashed box. Glowing corner brackets frame a warm light-field, and a
 * floating frosted "summon" capsule showing a morphing cat/dog line face invites
 * the tap. Once a photo is chosen it materializes inside the frame with a soft
 * refraction glow. Reads as capturing the pet into the 跟屁宠 light field.
 */
export function HoloCaptureFrame({
  photoUrl,
  label,
  hint,
  onClick,
}: {
  photoUrl?: string | null;
  label: string;
  hint: string;
  onClick?: () => void;
}) {
  return (
    <div
      className="relative aspect-[4/5] w-full max-w-[300px] cursor-pointer select-none"
      onClick={onClick}
      data-el="upload-dropzone"
    >
      {/* Inner light-field surface */}
      <div
        className="absolute inset-2 overflow-hidden rounded-[28px]"
        style={{
          background:
            "radial-gradient(120% 90% at 50% 20%, rgba(255,138,91,.18), transparent 60%), radial-gradient(90% 80% at 50% 100%, rgba(201,242,107,.12), transparent 62%), rgba(237,231,224,.06)",
          boxShadow: "inset 0 0 40px rgba(0,0,0,.25)",
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
        }}
      >
        {photoUrl ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={photoUrl}
              alt=""
              className="h-full w-full object-cover"
              style={{ animation: "petta-rise .5s ease both" }}
            />
            {/* Refraction sheen over the captured photo */}
            <span
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  "linear-gradient(115deg, rgba(255,255,255,.16) 0%, transparent 30%, transparent 70%, rgba(201,242,107,.14) 100%)",
                mixBlendMode: "screen",
              }}
            />
          </>
        ) : (
          <div className="absolute inset-0 grid place-items-center gap-3 px-5 text-center">
            {/* Floating frosted summon capsule + breathing halo */}
            <div
              className="relative grid place-items-center"
              style={{ transform: "translateY(5%)" }}
            >
              <span
                className="absolute h-48 w-48 rounded-full"
                style={{
                  background:
                    "radial-gradient(circle, rgba(201,242,107,.22), transparent 68%)",
                  animation: "petta-breathe 4s ease-in-out infinite",
                }}
              />
              <span
                className="relative grid h-32 w-32 place-items-center rounded-full border border-[var(--petta-line)] text-[var(--petta-cream)]"
                style={{
                  background: "var(--petta-glass)",
                  backdropFilter: "blur(14px)",
                  WebkitBackdropFilter: "blur(14px)",
                  boxShadow:
                    "inset 0 0 22px rgba(255,255,255,.14), 0 14px 40px rgba(0,0,0,.28)",
                  animation: "petta-floaty 5.5s ease-in-out infinite",
                }}
              >
                <PetFaceMorph size={56} />
              </span>
            </div>
            <span className="text-[15px] font-medium text-[var(--petta-cream)]">
              {label}
            </span>
            <small className="max-w-[16em] text-xs leading-relaxed text-[var(--petta-muted)]">
              {hint}
            </small>
          </div>
        )}
      </div>

      {/* Glowing corner brackets */}
      <Corner className="left-0 top-0" />
      <Corner className="right-0 top-0 rotate-90" />
      <Corner className="bottom-0 right-0 rotate-180" />
      <Corner className="bottom-0 left-0 -rotate-90" />
    </div>
  );
}

function Corner({ className }: { className?: string }) {
  return (
    <span
      className={cn("absolute h-9 w-9", className)}
      style={{
        borderTop: "2.5px solid var(--petta-lime)",
        borderLeft: "2.5px solid var(--petta-lime)",
        borderTopLeftRadius: "14px",
        filter: "drop-shadow(0 0 6px rgba(201,242,107,.6))",
        animation: "petta-corner-glow 2.6s ease-in-out infinite",
      }}
    />
  );
}
