import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: {
          bg: "#0f0f11",
          surface: "#1a1a1f",
          border: "#2d2d3e",
          accent: "#a78bfa",
          "accent-dim": "#7c3aed",
          text: "#e2e8f0",
          muted: "#94a3b8",
          success: "#4ade80",
          error: "#f87171",
          warning: "#fbbf24",
        },
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "'Fira Code'", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
