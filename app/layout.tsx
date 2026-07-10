import type { Metadata, Viewport } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Autonom.ia Sell",
  description: "A plataforma da Autonom.ia para criar, vender e entregar produtos digitais.",
};

export const viewport: Viewport = { themeColor: "#f4f6fb" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" data-scroll-behavior="smooth"><body>{children}</body></html>;
}
