import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle, for a container image (phase 6).
  output: "standalone",
};

export default nextConfig;
