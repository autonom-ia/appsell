import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.APPSELL_NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  reactStrictMode: true,
  outputFileTracingIncludes: {
    "/api/library/assets/[assetSlug]": ["./output/pdf/codex/*.pdf"]
  },
  logging: {
    incomingRequests: {
      ignore: [/\/auth\/login/, /\/auth\/callback/, /\/dev\/identity\/authorize/]
    }
  },
  experimental: {
    reactDebugChannel: false
  }
};

export default nextConfig;
