import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Inter, Montserrat, Space_Grotesk } from "next/font/google";
import { SITE } from "@/lib/site";

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

// A pagina publica usa Montserrat nos titulos (400 e 900 na mesma frase) e
// IBM Plex Mono nas linhas de numero.
const montserrat = Montserrat({
  subsets: ["latin"],
  weight: ["400", "600", "800", "900"],
  variable: "--font-montserrat",
  display: "swap"
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap"
});

// A coreografia so pode ser ligada antes da primeira pintura: se a classe
// chegasse junto com o gsap, o documento nasceria com uma altura e saltaria
// para outra ao terminar o download. O prazo de seguranca desfaz a classe
// caso a coreografia nunca assuma, para nao deixar dois atos invisiveis.
const LIGAR_MOTION = `(function(){try{
if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
var h=document.documentElement;h.classList.add('js-anim');
if('scrollRestoration' in history)history.scrollRestoration='manual';
setTimeout(function(){if(!h.dataset.motion)h.classList.remove('js-anim')},8000);
}catch(e){}})()`;

export const metadata: Metadata = {
  metadataBase: new URL(SITE.origem),
  title: "Autonom.ia Sell",
  description: "A plataforma da Autonom.ia para criar, vender e entregar produtos digitais.",
  icons: {
    icon: "/logos/autonomia_favicon.png",
    apple: "/logos/autonomia_favicon.png",
  },
};

export const viewport: Viewport = { themeColor: "#f4f6fb" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="pt-BR" data-scroll-behavior="smooth" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{ __html: LIGAR_MOTION }} /></head><body suppressHydrationWarning className={`${inter.variable} ${spaceGrotesk.variable} ${montserrat.variable} ${plexMono.variable}`}>{children}</body></html>;
}
