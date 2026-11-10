import type { Metadata, Viewport } from "next";
import "./globals.css";
import { cn } from "@/utils/utils";
import { Toaster } from "@/components/ui/sonner";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { LocaleSyncEffect } from "@/components/i18n/locale-sync-effect";
import { getServerLocale } from "@/lib/i18n/server-preference";

const SITE_URL = process.env.PUBLIC_APP_URL;

// Product metadata is provided by the self-hosted environment.
const SITE_TITLE = process.env.NEXT_PUBLIC_APP_TITLE?.trim() || "PETTA 全息随身宠物";
const SITE_DESCRIPTION =
  process.env.NEXT_PUBLIC_APP_DESCRIPTION?.trim() ||
  "上传宠物照片，生成动作并在全息设备上呈现。";

export const metadata: Metadata = {
  ...(SITE_URL ? { metadataBase: new URL(SITE_URL) } : {}),
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  icons: {
    icon: "/pet-icons/cat.png",
  },
  openGraph: {
    type: "website",
    siteName: "PETTA",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    url: "/",
    locale: "en_US",
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getServerLocale();

  return (
    <html
      lang={locale}
      suppressHydrationWarning
      className={cn("h-full antialiased", "font-sans")}
    >
      <body
        className="h-full flex flex-col"
      >
        <I18nProvider>
          <LocaleSyncEffect />
          {children}
          <Toaster />
        </I18nProvider>
      </body>
    </html>
  );
}
