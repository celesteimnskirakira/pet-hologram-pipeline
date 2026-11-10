import type { ReactNode } from "react";

import { LanguageSwitcher } from "@/components/i18n/language-switcher";
import { cn } from "@/utils/utils";

type ErrorPageShellProps = {
  children: ReactNode;
  className?: string;
};

export function ErrorPageShell({ children, className }: ErrorPageShellProps) {
  return (
    <div
      className={cn(
        "relative flex min-h-[100dvh] flex-col items-center justify-center overflow-hidden bg-background px-6 py-16",
        className,
      )}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,oklch(0.92_0.02_250/0.45),transparent)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35] [background-image:linear-gradient(to_right,oklch(0.85_0_0/0.12)_1px,transparent_1px),linear-gradient(to_bottom,oklch(0.85_0_0/0.12)_1px,transparent_1px)] [background-size:3rem_3rem]"
      />

      <header className="absolute right-4 top-4 z-10 flex items-center gap-2">
        <LanguageSwitcher />
      </header>

      <div className="relative z-[1] w-full max-w-md">{children}</div>
    </div>
  );
}
