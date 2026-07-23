import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Pin the workspace root to this folder so Turbopack never resolves
  // dependencies from the parent (Python) project directory.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
