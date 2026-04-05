/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // ── Theme-switchable surfaces (backed by CSS variables) ────────────
        bg:             "rgb(var(--c-bg) / <alpha-value>)",
        surface:        "rgb(var(--c-surface) / <alpha-value>)",
        raised:         "rgb(var(--c-raised) / <alpha-value>)",
        border:         "rgb(var(--c-border) / <alpha-value>)",
        "border-strong":"rgb(var(--c-border-strong) / <alpha-value>)",

        // ── Text hierarchy ─────────────────────────────────────────────────
        "text-1": "rgb(var(--c-text-1) / <alpha-value>)",
        "text-2": "rgb(var(--c-text-2) / <alpha-value>)",
        "text-3": "rgb(var(--c-text-3) / <alpha-value>)",
        "text-4": "rgb(var(--c-text-4) / <alpha-value>)",

        // ── Semantic (theme-switchable) ────────────────────────────────────
        "success-bg":     "rgb(var(--c-success-bg) / <alpha-value>)",
        "success-text":   "rgb(var(--c-success-text) / <alpha-value>)",
        "success-border": "rgb(var(--c-success-border) / <alpha-value>)",
        "warning-bg":     "rgb(var(--c-warning-bg) / <alpha-value>)",
        "warning-text":   "rgb(var(--c-warning-text) / <alpha-value>)",
        "warning-border": "rgb(var(--c-warning-border) / <alpha-value>)",
        "danger-bg":      "rgb(var(--c-danger-bg) / <alpha-value>)",
        "danger-text":    "rgb(var(--c-danger-text) / <alpha-value>)",
        "danger-border":  "rgb(var(--c-danger-border) / <alpha-value>)",
        "critical-bg":    "rgb(var(--c-critical-bg) / <alpha-value>)",
        "critical-text":  "rgb(var(--c-critical-text) / <alpha-value>)",
        "critical-border":"rgb(var(--c-critical-border) / <alpha-value>)",

        // ── Fixed (same in both themes) ────────────────────────────────────
        accent:         "#4f46e5",
        "accent-hover": "#4338ca",
        "accent-light": "#6366f1",
        "accent-muted": "rgba(79,70,229,0.10)",
        "accent-text":  "#4338ca",

        success:  "#16a34a",
        warning:  "#d97706",
        danger:   "#dc2626",
        critical: "#b91c1c",

        // ── Sidebar (always dark) ──────────────────────────────────────────
        "side-bg":     "#18191c",
        "side-hover":  "#26282e",
        "side-active": "#26282e",
        "side-border": "#26282e",
        "side-text":   "#e0e1e4",
        "side-muted":  "#646670",
        "side-accent": "#818cf8",
      },

      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "monospace"],
      },

      boxShadow: {
        sm:    "0 1px 2px rgb(var(--c-shadow) / 0.06)",
        card:  "0 1px 3px rgb(var(--c-shadow) / 0.08), 0 1px 2px rgb(var(--c-shadow) / 0.04)",
        md:    "0 4px 12px rgb(var(--c-shadow) / 0.08), 0 2px 4px rgb(var(--c-shadow) / 0.04)",
        lg:    "0 8px 24px rgb(var(--c-shadow) / 0.10), 0 4px 8px rgb(var(--c-shadow) / 0.06)",
        panel: "0 16px 40px rgb(var(--c-shadow) / 0.14), 0 4px 12px rgb(var(--c-shadow) / 0.08)",
        glow:  "0 0 0 3px rgba(79,70,229,0.18)",
      },

      animation: {
        "slide-in": "slideIn 0.28s cubic-bezier(0.16,1,0.3,1)",
        "fade-in":  "fadeIn 0.18s ease-out",
        "slide-up": "slideUp 0.2s ease-out",
      },

      keyframes: {
        slideIn: {
          from: { transform: "translateX(100%)", opacity: "0" },
          to:   { transform: "translateX(0)",    opacity: "1" },
        },
        fadeIn: {
          from: { opacity: "0" },
          to:   { opacity: "1" },
        },
        slideUp: {
          from: { transform: "translateY(6px)", opacity: "0" },
          to:   { transform: "translateY(0)",   opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};
