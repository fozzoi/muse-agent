/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Geist"', '"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"Geist Mono"', '"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      keyframes: {
        "fade-in": { from: { opacity: 0, transform: "translateY(4px)" }, to: { opacity: 1, transform: "none" } },
        "pop-in": { from: { opacity: 0, transform: "scale(.97)" }, to: { opacity: 1, transform: "none" } },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgba(34,211,238,.55)" },
          "100%": { boxShadow: "0 0 0 8px rgba(34,211,238,0)" },
        },
      },
      animation: {
        "fade-in": "fade-in .2s ease-out",
        "pop-in": "pop-in .15s ease-out",
        "pulse-ring": "pulse-ring 1.4s ease-out infinite",
      },
    },
  },
  plugins: [],
};