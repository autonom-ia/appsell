import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.APPSELL_NEXT_DIST_DIR || ".next",
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    root: process.cwd()
  },
  logging: {
    incomingRequests: {
      ignore: [
        /\/auth\/login/,
        /\/auth\/callback/,
        /\/dev\/identity\/authorize/,
        /\/api\/webhooks\/checkouts\//
      ]
    }
  },
  experimental: {
    reactDebugChannel: false
  },
  // Recebimentos viraram uma área de Configurações (#352). O endereço antigo
  // está em links salvos e em e-mails já enviados; ele continua chegando.
  async redirects() {
    return [{ source: "/payouts", destination: "/settings/payouts", permanent: true }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          ...(process.env.NODE_ENV === "production"
            ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]
            : []),
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Cross-Origin-Resource-Policy", value: "same-site" }
        ]
      },
      // As imagens públicas da loja são carregadas por páginas de outro
      // domínio (o checkout do Financial), então a regra acima, que é
      // `same-site`, as bloquearia. A resposta de cada rota já pede
      // `cross-origin`, mas a configuração do Next vence: sem a exceção aqui,
      // o cabeçalho da rota não chega ao navegador. Vale para as três, não só
      // para a capa (#412).
      ...["/api/storefronts/:sellerSlug/brand/:kind",
          "/api/storefronts/:sellerSlug/products/:productSlug/cover",
          "/api/storefronts/:sellerSlug/products/:productSlug/banner"].map((source) => ({
        source,
        headers: [
          { key: "Cross-Origin-Resource-Policy", value: "cross-origin" }
        ]
      }))
    ];
  }
};

export default nextConfig;
