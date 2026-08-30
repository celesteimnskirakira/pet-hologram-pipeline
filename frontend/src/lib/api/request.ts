"use client";

import { getResolvedLocale } from "@/i18n";

/**
 * Browser request wrapper for the standalone app. Authentication can be added
 * here later without exposing generation-service credentials to the browser.
 */
export async function request(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("x-app-locale", getResolvedLocale());

  return fetch(input, {
    ...init,
    headers,
  });
}
