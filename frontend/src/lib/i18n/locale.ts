export const localeCodes = ["en-US", "zh-CN"] as const;
export type LocaleCode = (typeof localeCodes)[number];

const localeSet = new Set<string>(localeCodes);

export const normalizeLocale = (value: string | null | undefined): LocaleCode | null => {
  if (!value) return null;
  if (localeSet.has(value)) return value as LocaleCode;

  const normalized = value.toLowerCase();
  if (normalized.startsWith("zh")) return "zh-CN";
  if (normalized.startsWith("en")) return "en-US";
  return null;
};
