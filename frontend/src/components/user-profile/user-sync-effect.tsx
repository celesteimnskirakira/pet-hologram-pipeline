"use client";

import { useEffect, useRef } from "react";
import { auth } from "@eazo/sdk";
import { useEazo } from "@eazo/sdk/react";

/**
 * Mobile-only: hits /api/user/profile once after login to upsert the user
 * into the local DB. Web doesn't need this — the SDK already calls the same
 * endpoint during web bootstrap; mobile bootstraps from the bridge `hello`
 * instead and never auto-fetches profile, so the upsert has to be triggered
 * manually here.
 */
export function UserSyncEffect() {
  const authenticated = useEazo((s) => s.auth.authenticated);
  const platform = useEazo((s) => s.device.platform);
  const syncedUserId = useRef<string | null>(null);

  useEffect(() => {
    if (!authenticated || platform !== "mobile") return;

    const userId = auth.user?.id ?? null;
    if (!userId || syncedUserId.current === userId) return;

    syncedUserId.current = userId;

      (async () => {
      try {
        const sessionHeader = await auth.getSessionHeader();
        if (!sessionHeader) return;

        await fetch("/api/user/profile", {
          headers: { "x-eazo-session": sessionHeader },
        });
      } catch (err) {
        console.error("[UserSyncEffect] profile fetch failed", err);
      }
    })();
  }, [authenticated, platform]);

  return null;
}
