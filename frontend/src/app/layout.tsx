import type { Metadata, Viewport } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "BruteApply — Job search operations",
  description: "A private, owner-controlled workspace for managing a focused IT job search.",
  robots: { index: false, follow: false, noarchive: true },
};

export const viewport: Viewport = { themeColor: "#101b2b", width: "device-width", initialScale: 1 };
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
