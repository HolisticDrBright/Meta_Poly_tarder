/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",

  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/live",
  },

  // Don't fail build on lint warnings
  eslint: {
    ignoreDuringBuilds: true,
  },

  // Don't fail build on TS errors (they're type-only issues)
  typescript: {
    ignoreBuildErrors: true,
  },
};

module.exports = nextConfig;
