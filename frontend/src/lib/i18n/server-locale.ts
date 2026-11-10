import type { NextRequest } from "next/server";
import { normalizeLocale, type LocaleCode } from "@/lib/i18n/locale";

export function getRequestLocale(request: NextRequest): LocaleCode {
  const fromHeader = normalizeLocale(request.headers.get("x-app-locale"));
  if (fromHeader) return fromHeader;

  const acceptLanguage = request.headers.get("accept-language");
  const preferred = acceptLanguage?.split(",")[0]?.split(";")[0]?.trim();
  return normalizeLocale(preferred) ?? "en-US";
}
