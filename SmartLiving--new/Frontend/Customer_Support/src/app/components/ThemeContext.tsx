import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export const LIGHT = {
  pageBg: "radial-gradient(circle at top left, #f4f1ff 0%, #f6f3ff 22%, #f9f7ff 50%, #f5f6fb 78%, #f3f4f8 100%)",
  surface: "rgba(255,255,255,0.78)",
  surfaceHover: "rgba(244,239,255,0.95)",
  surfaceActive: "#ebe3ff",
  surfaceSubtle: "#fbfaff",
  surfacePrimary: "#f2eeff",

  sidebarBg: "linear-gradient(180deg, #191428 0%, #171124 52%, #0e0a18 100%)",
  sidebarBorder: "rgba(255,255,255,0.08)",
  sidebarActiveBg: "linear-gradient(135deg, rgba(116,92,255,0.92), rgba(146,124,255,0.82))",
  sidebarActiveText: "#ffffff",
  sidebarText: "rgba(255,255,255,0.66)",
  sidebarTextHover: "#ffffff",
  sidebarGroupLabel: "rgba(255,255,255,0.38)",

  navBg: "rgba(255,255,255,0.72)",
  navBorder: "rgba(122,99,255,0.12)",
  navShadow: "0 18px 42px rgba(42,25,90,0.10)",

  border: "rgba(122,99,255,0.12)",
  borderSubtle: "rgba(122,99,255,0.08)",
  borderInput: "rgba(122,99,255,0.18)",

  textPrimary: "#17142b",
  textSecondary: "#3f3a58",
  textTertiary: "#706a86",
  textMuted: "#9e98b3",
  textDisabled: "#c8c3d8",

  inputBg: "rgba(255,255,255,0.82)",
  inputBgFocus: "#ffffff",
  inputBorder: "rgba(122,99,255,0.18)",
  inputBorderFocus: "#6852ff",
  inputRing: "rgba(104,82,255,0.14)",

  accent: "#6852ff",
  accentHover: "#5743df",
  accentSubtle: "#ebe6ff",
  accentBg: "#f3efff",
  accentBorder: "#cdc2ff",

  successText: "#15803d",
  successBg: "#dcfce7",
  warnText: "#b45309",
  warnBg: "#fef3c7",
  dangerText: "#dc2626",
  dangerBg: "#fee2e2",
  infoText: "#1d4ed8",
  infoBg: "#dbeafe",

  tableHeaderBg: "#faf8ff",
  tableRowHover: "#f5f1ff",
  tableDivider: "rgba(122,99,255,0.10)",

  cardBg: "rgba(255,255,255,0.78)",
  cardBorder: "rgba(122,99,255,0.10)",
  cardShadow: "0 20px 45px rgba(42,25,90,0.10)",
  cardRadius: "26px",

  tooltipBg: "#151126",
  tooltipText: "#ffffff",

  overlayBg: "rgba(17,12,34,0.46)",
};

export const DARK = {
  pageBg: "radial-gradient(circle at top left, #161125 0%, #120e1d 42%, #09070f 100%)",
  surface: "rgba(21,17,38,0.90)",
  surfaceHover: "rgba(31,25,52,0.94)",
  surfaceActive: "rgba(104,82,255,0.18)",
  surfaceSubtle: "#110d1c",
  surfacePrimary: "rgba(104,82,255,0.10)",

  sidebarBg: "linear-gradient(180deg, #09070f 0%, #100a1a 48%, #171128 100%)",
  sidebarBorder: "rgba(255,255,255,0.06)",
  sidebarActiveBg: "linear-gradient(135deg, rgba(104,82,255,0.90), rgba(71,54,178,0.92))",
  sidebarActiveText: "#ffffff",
  sidebarText: "rgba(230,225,255,0.64)",
  sidebarTextHover: "#ffffff",
  sidebarGroupLabel: "rgba(255,255,255,0.30)",

  navBg: "rgba(17,13,28,0.84)",
  navBorder: "rgba(255,255,255,0.07)",
  navShadow: "0 16px 36px rgba(0,0,0,0.24)",

  border: "rgba(255,255,255,0.08)",
  borderSubtle: "rgba(255,255,255,0.06)",
  borderInput: "rgba(255,255,255,0.12)",

  textPrimary: "#f5f0ff",
  textSecondary: "#d4caee",
  textTertiary: "#9d91c2",
  textMuted: "#746991",
  textDisabled: "#4a4261",

  inputBg: "rgba(255,255,255,0.05)",
  inputBgFocus: "rgba(255,255,255,0.08)",
  inputBorder: "rgba(255,255,255,0.10)",
  inputBorderFocus: "#8d7bff",
  inputRing: "rgba(141,123,255,0.16)",

  accent: "#8d7bff",
  accentHover: "#a899ff",
  accentSubtle: "rgba(141,123,255,0.14)",
  accentBg: "rgba(141,123,255,0.09)",
  accentBorder: "rgba(141,123,255,0.28)",

  successText: "#4ade80",
  successBg: "rgba(74,222,128,0.08)",
  warnText: "#fcd34d",
  warnBg: "rgba(252,211,77,0.08)",
  dangerText: "#f87171",
  dangerBg: "rgba(248,113,113,0.08)",
  infoText: "#60a5fa",
  infoBg: "rgba(96,165,250,0.08)",

  tableHeaderBg: "rgba(255,255,255,0.03)",
  tableRowHover: "rgba(255,255,255,0.04)",
  tableDivider: "rgba(255,255,255,0.06)",

  cardBg: "rgba(21,17,38,0.90)",
  cardBorder: "rgba(255,255,255,0.07)",
  cardShadow: "0 22px 46px rgba(0,0,0,0.28)",
  cardRadius: "26px",

  tooltipBg: "#f5f0ff",
  tooltipText: "#110d1c",

  overlayBg: "rgba(2,0,8,0.68)",
};

export type Theme = typeof LIGHT;

interface ThemeCtx {
  isDark: boolean;
  toggle: () => void;
  t: Theme;
}

const ThemeContext = createContext<ThemeCtx>({
  isDark: false,
  toggle: () => {},
  t: LIGHT,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [isDark, setIsDark] = useState(() => {
    try {
      return localStorage.getItem("scos-theme") === "dark";
    } catch {
      return false;
    }
  });

  const toggle = () =>
    setIsDark((current) => {
      const next = !current;
      try {
        localStorage.setItem("scos-theme", next ? "dark" : "light");
      } catch {}
      return next;
    });

  useEffect(() => {
    const root = document.documentElement;
    if (isDark) {
      root.classList.add("dark");
      root.setAttribute("data-theme", "dark");
    } else {
      root.classList.remove("dark");
      root.setAttribute("data-theme", "light");
    }
  }, [isDark]);

  return (
    <ThemeContext.Provider value={{ isDark, toggle, t: isDark ? DARK : LIGHT }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
