import type { Metadata } from "next";
import { JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { cn } from "@/lib/utils";
import { AuthProvider } from "@/context/auth-context";
import { Providers } from "@/context/providers";
import { ThemeProvider } from "@/context/theme-provider";
import { HealthCheckProvider } from "@/context/health-check-provider";
import { Toaster } from "@/components/ui/sonner";
import { DynamicFavicon } from "@/components/shared/DynamicFavicon";
import { ColorModeProvider } from "@/context/color-mode-provider";
import { APP_NAME, APP_TITLE, DEFAULT_FAVICON, FAVICON_URL } from "@/utils/brand-env";

const jetbrainsMono = JetBrains_Mono({subsets:['latin'],variable:'--font-mono'});

export const metadata: Metadata = {
  title: { default: APP_TITLE, template: `%s · ${APP_NAME}` },
  description: "GPU control plane: clusters, models, rules, metering and billing for your own hardware",
  // Favicon from env (the GPU Deck mark by default); DynamicFavicon probes it on the client.
  icons:
    FAVICON_URL === DEFAULT_FAVICON
      ? {
          icon: [
            { url: "/favicon.ico", sizes: "16x16 32x32 48x48" },
            { url: "/favicon.svg", type: "image/svg+xml" },
          ],
          apple: "/apple-touch-icon.png",
        }
      : { icon: FAVICON_URL, apple: FAVICON_URL },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      data-scroll-behavior="smooth"
      className={cn("h-full", "antialiased", jetbrainsMono.variable)}
      suppressHydrationWarning
    >
      <head>
        {/* Satoshi (default typeface) — Fontshare CDN. A CSS @import of this
            URL gets silently stripped by Turbopack, so it's loaded as a <link>.
            referrerPolicy is required: Fontshare returns an "access
            restricted" stub CSS (still HTTP 200, so it fails silently) when
            the request carries a browser Referer header. */}
        <link rel="preconnect" href="https://api.fontshare.com" />
        <link
          rel="stylesheet"
          href="https://api.fontshare.com/v2/css?f[]=satoshi@300,400,500,700,900&display=swap"
          referrerPolicy="no-referrer"
        />
      </head>
      <body className={cn("min-h-full flex flex-col")}>
        <ColorModeProvider>
          <Providers>
            <HealthCheckProvider>
              <AuthProvider>
                <ThemeProvider>
                  <DynamicFavicon />
                  {children}
                  <Toaster />
                </ThemeProvider>
              </AuthProvider>
            </HealthCheckProvider>
          </Providers>
        </ColorModeProvider>
      </body>
    </html>
  );
}
