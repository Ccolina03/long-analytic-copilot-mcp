import type { Metadata } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Sans, IBM_Plex_Serif } from "next/font/google";
import "./globals.css";

const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sans",
});
const serif = IBM_Plex_Serif({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-serif",
});

export const metadata: Metadata = {
  title: "SME Network",
  description:
    "Domain experts that discover who owns the blast radius, skip everyone else, and land a design 1-pager.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable}`}>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
