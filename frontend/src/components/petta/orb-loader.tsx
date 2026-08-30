"use client";

import { cn } from "@/utils/utils";

/**
 * PettaOrbLoader — the signature "high-end, simple, looping" loading motion.
 *
 * A frosted glass core inside concentric breathing halos, wrapped by two
 * counter-rotating dashed rings and a slow-drifting particle field. The center
 * can host the uploaded photo (during upload) or a soft pet silhouette glyph
 * (during generation). Every layer loops seamlessly with no start/end seam.
 */
export function PettaOrbLoader({
  size = 220,
  photoUrl,
  active = true,
  className,
}: {
  size?: number;
  photoUrl?: string | null;
  active?: boolean;
  className?: string;
}) {
  const ringPause = active ? "running" : "paused";
  return (
    <div
      className={cn("relative grid place-items-center", className)}
      style={{ width: size, height: size }}
      data-el="petta-orb-loader"
      aria-hidden
    >
      {/* Breathing halos */}
      {[1, 0.72, 0.5].map((scale, i) => (
        <span
          key={i}
          className="absolute rounded-full"
          style={{
            width: size * scale,
            height: size * scale,
            background:
              i === 0
                ? "radial-gradient(circle, rgba(201,242,107,.16), transparent 68%)"
                : "radial-gradient(circle, rgba(255,138,91,.14), transparent 66%)",
            animation: `petta-breathe ${4.5 + i * 1.1}s ease-in-out infinite`,
            animationDelay: `${i * 0.5}s`,
            animationPlayState: ringPause,
          }}
        />
      ))}

      {/* Counter-rotating dashed rings */}
      <svg
        className="absolute"
        width={size}
        height={size}
        viewBox="0 0 100 100"
        style={{ animation: `petta-spin 22s linear infinite`, animationPlayState: ringPause }}
      >
        <circle
          cx="50"
          cy="50"
          r="46"
          fill="none"
          stroke="var(--petta-line)"
          strokeWidth="0.6"
          strokeDasharray="1.5 5"
        />
      </svg>
      <svg
        className="absolute"
        width={size * 0.82}
        height={size * 0.82}
        viewBox="0 0 100 100"
        style={{ animation: `petta-orbit 30s linear infinite`, animationPlayState: ringPause }}
      >
        <circle
          cx="50"
          cy="50"
          r="46"
          fill="none"
          stroke="rgba(201,242,107,.5)"
          strokeWidth="0.8"
          strokeDasharray="0.5 9"
          strokeLinecap="round"
        />
      </svg>

      {/* Orbiting accent dots */}
      <div
        className="absolute inset-0"
        style={{ animation: `petta-spin 14s linear infinite`, animationPlayState: ringPause }}
      >
        <span
          className="absolute left-1/2 top-0 h-2 w-2 -translate-x-1/2 rounded-full"
          style={{ background: "var(--petta-lime)", boxShadow: "0 0 12px var(--petta-lime)" }}
        />
      </div>
      <div
        className="absolute inset-0"
        style={{ animation: `petta-orbit 19s linear infinite`, animationPlayState: ringPause }}
      >
        <span
          className="absolute bottom-[8%] left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full"
          style={{ background: "var(--petta-peach)", boxShadow: "0 0 10px var(--petta-peach)" }}
        />
      </div>

      {/* Frosted glass core */}
      <div
        className="relative grid place-items-center overflow-hidden rounded-full border border-[var(--petta-line)]"
        style={{
          width: size * 0.44,
          height: size * 0.44,
          background: "var(--petta-glass)",
          backdropFilter: "blur(18px)",
          WebkitBackdropFilter: "blur(18px)",
          boxShadow:
            "inset 0 0 26px rgba(255,255,255,.12), 0 18px 50px rgba(0,0,0,.28)",
        }}
      >
        {photoUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={photoUrl}
            alt=""
            className="h-full w-full object-cover"
            style={{ animation: "petta-breathe 5s ease-in-out infinite", animationPlayState: ringPause }}
          />
        ) : (
          <PetGlyph
            className="h-1/2 w-1/2 text-[var(--petta-cream)]"
            style={{ animation: "petta-breathe 4s ease-in-out infinite", animationPlayState: ringPause }}
          />
        )}
      </div>
    </div>
  );
}

function PetGlyph({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      style={style}
      aria-hidden
    >
      <path
        d="M24 40c-7 0-13-4.5-13-12 0-6 4-11 13-11s13 5 13 11c0 7.5-6 12-13 12Z"
        fill="currentColor"
        opacity="0.9"
      />
      <ellipse cx="14" cy="14" rx="3.4" ry="5" fill="currentColor" />
      <ellipse cx="34" cy="14" rx="3.4" ry="5" fill="currentColor" />
      <circle cx="19" cy="27" r="1.6" fill="var(--petta-bg)" />
      <circle cx="29" cy="27" r="1.6" fill="var(--petta-bg)" />
    </svg>
  );
}
