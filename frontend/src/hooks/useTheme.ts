import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem("infrawatch-theme") as Theme | null;
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("infrawatch-theme", theme);
  }, [theme]);

  const toggleTheme = () => setThemeState((t) => (t === "light" ? "dark" : "light"));

  return { theme, toggleTheme };
}
