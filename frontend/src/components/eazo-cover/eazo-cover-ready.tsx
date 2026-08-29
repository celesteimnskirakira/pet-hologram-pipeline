"use client";

import { useEffect } from "react";

/**
 * Signals to the cover-capture service that the preview has painted and is
 * ready to record. Sets a stable marker attribute on <body>. Self-contained;
 * mounts no auth/provider code.
 */
export function EazoCoverReady({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    document.body.setAttribute("data-eazo-cover-ready", "1");
    return () => {
      document.body.removeAttribute("data-eazo-cover-ready");
    };
  }, []);
  return <>{children}</>;
}
