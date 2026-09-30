import type { Config } from "tailwindcss";
export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { cyan: "#00F2EA", pink: "#FE2C55", ink: "#0B0B10", panel: "#121218" },
      },
    },
  },
  plugins: [],
} satisfies Config;
