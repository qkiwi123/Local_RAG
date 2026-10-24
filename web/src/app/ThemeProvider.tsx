import { useLayoutEffect, useState, type ReactNode } from "react";
import { ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { ThemeContext, type ThemeMode } from "./ThemeContext";

const STORAGE_KEY = "rag.appearance.theme";

function initialMode(): ThemeMode {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* Storage may be disabled; the switch still works in this page. */ }
  return window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(initialMode);
  const dark = mode === "dark";
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
    try { window.localStorage.setItem(STORAGE_KEY, mode); } catch { /* Optional preference persistence. */ }
  }, [mode]);

  return (
    <ThemeContext.Provider value={{ mode, toggle: () => setMode((current) => current === "light" ? "dark" : "light") }}>
      <ConfigProvider locale={zhCN} theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: dark ? "#91a7ff" : "#4263df", colorInfo: dark ? "#91a7ff" : "#4263df",
          colorSuccess: dark ? "#62cba5" : "#2c8a68",
          colorText: dark ? "#d9e2f2" : "#27344b", colorTextSecondary: dark ? "#9aaac2" : "#738096",
          colorBorder: dark ? "#334057" : "#e0e5ed",
          colorBgBase: dark ? "#101722" : "#ffffff",
          colorBgContainer: dark ? "#182131" : "#ffffff",
          colorBgElevated: dark ? "#202b3d" : "#ffffff",
          colorBgLayout: dark ? "#101722" : "#f3f5f9",
          borderRadius: 8, controlHeight: 36, fontSize: 14,
          fontFamily: '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
        components: {
          Card: { headerFontSize: 16 },
          Table: { headerBg: dark ? "#202b3d" : "#f7f8fb", headerColor: dark ? "#a9b9cf" : "#78849a", rowHoverBg: dark ? "#232f45" : "#f7f9fe" },
          Button: { primaryShadow: dark ? "none" : "0 3px 8px rgb(66 99 223 / 16%)" },
        },
      }}>{children}</ConfigProvider>
    </ThemeContext.Provider>
  );
}
