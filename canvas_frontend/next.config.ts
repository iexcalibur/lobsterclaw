import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",        // static export → out/ directory served by FastAPI
  trailingSlash: true,     // required for static export with dynamic routes
  images: {
    unoptimized: true,     // required for static export
  },
};

export default nextConfig;
