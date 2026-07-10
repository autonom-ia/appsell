import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = { title: "Autonom.ia Sell — OAuth BFF Spike" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR"><body>{children}</body></html>;
}
