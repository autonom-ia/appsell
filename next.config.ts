import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
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
