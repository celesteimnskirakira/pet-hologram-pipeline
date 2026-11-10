"use client";

import { useEffect } from "react";
import { I18nextProvider } from "react-i18next";
import i18n, { applyStoredLocalePreference } from "@/i18n";

export function I18nProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    void applyStoredLocalePreference();
  }, []);

  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
