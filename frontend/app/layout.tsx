import "./globals.css";
import type { Metadata, Viewport } from "next";
export const metadata: Metadata = { title: "KLIPANI", description: "Локальная студия TikTok-клипов", manifest: "/manifest.json", appleWebApp: { capable: true, title: "KLIPANI", statusBarStyle: "black-translucent" }, icons: { icon: "/icon.png", apple: "/icon.png" } };
export const viewport: Viewport = { themeColor: "#8b5cf6", width: "device-width", initialScale: 1 };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="ru"><body>{children}</body></html>; }
