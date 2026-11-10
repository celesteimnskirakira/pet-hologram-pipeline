"use client";

import { useEffect, useState } from "react";

/**
 * PetFaceMorph — cross-fades between a cat-head and dog-head line icon on a
 * loop, hinting that any pet can be summoned. Uses the brand line-art PNGs from
 * /public/pet-icons, recolored to the cream tone to read on the dark glass.
 */
export function PetFaceMorph({
  size = 26,
  intervalMs = 2000,
}: {
  size?: number;
  intervalMs?: number;
}) {
  const [isDog, setIsDog] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setIsDog((v) => !v), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  // Recolor the dark navy line art to the cream tone so it reads on dark glass.
  const creamFilter =
    "brightness(0) invert(92%) sepia(6%) saturate(220%) hue-rotate(2deg)";

  return (
    <span
      className="relative inline-grid"
      style={{ width: size, height: size }}
      aria-hidden
    >
      {/* eslint-disable @next/next/no-img-element */}
      <img
        src="/pet-icons/cat.png"
        alt=""
        width={size}
        height={size}
        className="col-start-1 row-start-1 transition-opacity duration-700"
        style={{ opacity: isDog ? 0 : 1, filter: creamFilter }}
      />
      <img
        src="/pet-icons/dog.png"
        alt=""
        width={size}
        height={size}
        className="col-start-1 row-start-1 transition-opacity duration-700"
        style={{ opacity: isDog ? 1 : 0, filter: creamFilter }}
      />
      {/* eslint-enable @next/next/no-img-element */}
    </span>
  );
}
