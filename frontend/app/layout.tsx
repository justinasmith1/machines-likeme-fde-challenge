import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Contoso Invoice Matching",
  description: "Three-way matching of supplier invoices with human review",
};

// Rendered per request, so API_BASE_URL is read when the server runs, not when the image
// is built. One container image then works in every environment.
export const dynamic = "force-dynamic";

const DEFAULT_API_BASE_URL = "http://localhost:8000/api";

export default function RootLayout({ children }: { children: ReactNode }) {
  // Absolute ("https://api.example/api") or relative ("/api") when both share an origin.
  const apiBaseUrl = process.env.API_BASE_URL || DEFAULT_API_BASE_URL;
  return (
    <html lang="en">
      <body>
        <Providers apiBaseUrl={apiBaseUrl}>{children}</Providers>
      </body>
    </html>
  );
}
