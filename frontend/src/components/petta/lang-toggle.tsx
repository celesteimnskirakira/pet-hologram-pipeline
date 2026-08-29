"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import {
  changeLocale,
  getLocalePreference,
  normalizeLocale,
  type LocaleCode,
  type LocalePreference,
} from "@/i18n";
import { cn } from "@/utils/utils";

/**
 * Compact PETTA-styled EN / 中文 toggle rendered in the shell's top-right.
 * Wired to changeLocale / getLocalePreference so viewers can switch language.
 */
export function LangToggle({ className }: { className?: string }) {
  const { i18n } = useTranslation();

  const subscribe = useCallback(
    (sync: () => void) => {
      i18n.on("languageChanged", sync);
      window.addEventListener("eazo-locale-preference-changed", sync);
      window.addEventListener("storage", sync);
      return () => {
        i18n.off("languageChanged", sync);
        window.removeEventListener("eazo-locale-preference-changed", sync);
        window.removeEventListener("storage", sync);
      };
    },
    [i18n],
  );

  useSyncExternalStore(
    subscribe,
    getLocalePreference,
    () => "system" as LocalePreference,
  );

  const active = normalizeLocale(i18n.resolvedLanguage || i18n.language) ?? "en-US";

  const options: { code: LocaleCode; label: string }[] = [
    { code: "zh-CN", label: "中文" },
    { code: "en-US", label: "EN" },
  ];

  return (
    <div
      className={cn(
        "flex items-center gap-0.5 rounded-full border border-[var(--petta-line)] bg-[var(--petta-glass)] p-0.5 backdrop-blur-md",
        className,
      )}
      style={{ WebkitBackdropFilter: "blur(12px)" }}
      data-el="lang-toggle"
    >
      {options.map((o) => (
        <button
          key={o.code}
          type="button"
          onClick={() => void changeLocale(o.code)}
          className={cn(
            "rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors",
            active === o.code
              ? "bg-[var(--petta-cream)] text-[#23170f]"
              : "text-[var(--petta-muted)]",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
