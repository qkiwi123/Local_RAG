import { createContext, useContext } from "react";

export type ThemeMode = "light" | "dark";
export const ThemeContext = createContext<{ mode: ThemeMode; toggle: () => void }>({ mode: "light", toggle: () => {} });
export function useAppTheme() { return useContext(ThemeContext); }
