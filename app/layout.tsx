import type { Metadata, Viewport } from "next";
import { Inter, Space_Grotesk } from "next/font/google";
import "./styles.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap"
});

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space-grotesk",
  display: "swap"
});

export const metadata: Metadata = {
  title: "Autonom.ia Sell",
  description: "A plataforma da Autonom.ia para criar, vender e entregar produtos digitais.",
};

export const viewport: Viewport = { themeColor: "#f4f6fb" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" data-scroll-behavior="smooth"><body className={`${inter.variable} ${spaceGrotesk.variable}`}>{children}</body></html>;
}
