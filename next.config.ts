import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.APPSELL_NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  reactStrictMode: true,
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
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Cross-Origin-Resource-Policy", value: "same-site" }
        ]
      }
    ];
  }
};

export default nextConfig;
