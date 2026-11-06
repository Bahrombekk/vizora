import { createContext, useContext, useEffect, useState } from "react";

// "Vizora" dizayn yo'nalishi — korporativ, iliq va tinch: qog'oz-oq fon, grafit matn,
// to'q-sariq (amber) urg'u, ixcham burchaklar, yuqori gorizontal navigatsiya.
// Komponentlar faqat shu tokenlarni ishlatadi (CSS'da `var(--x)` / `rgb(var(--xRgb) / a)`).
const SHAPE = {
  radius: "10px", radiusSm: "8px", radiusLg: "14px", radiusPill: "999px", radiusXs: "6px",
  badgeTransform: "none",
  fontDisplay: "'Sora',sans-serif", fontBody: "'Manrope',sans-serif", mono: "'IBM Plex Mono',monospace",
};
export const THEMES = {
  light: {
    ...SHAPE,
    bg: "#f5f3ee", bgTint: "#efece5", surface: "#ffffff", surface2: "#f8f6f1", surface3: "#f1eee7",
    border: "#e6e1d6", borderStrong: "#d3ccbe", borderSoft: "#eeeae2",
    text: "#1c1a17", dim: "#5f5a50", faint: "#9a9488",
    accent: "#e2580b", accent2: "#d97706", accentText: "#ffffff",
    accentRgb: "226 88 11", accent2Rgb: "217 119 6", onlineRgb: "22 128 61",
    accentSoft: "rgba(226,88,11,.09)", accentSoft2: "rgba(217,119,6,.08)", accentLine: "rgba(226,88,11,.3)",
    accentGrad: "linear-gradient(135deg,#f0782a 0%,#e2580b 60%,#c94a06 100%)",
    online: "#16803d", offline: "#8b857a", error: "#b7791f", warn: "#b7791f", danger: "#c8372d",
    onlineBg: "rgba(22,128,61,.10)", offlineBg: "rgba(139,133,122,.14)", errorBg: "rgba(183,121,31,.12)",
    dangerBg: "rgba(200,55,45,.10)", infoBg: "rgba(37,99,235,.09)", info: "#2563eb",
    // Navigatsiya paneli (yuqori) — yorug' mavzuda ham to'q grafit, brend shu yerda
    sidebar: "#1f1d1a", sidebar2: "#191714", sidebarBorder: "#2e2b27", sidebarBrand: "#faf8f4",
    sidebarItem: "#b8b1a4", sidebarMuted: "#857e72", sidebarHover: "rgba(255,255,255,.06)",
    sidebarActiveBg: "rgba(240,120,42,.16)", sidebarActiveBar: "#f0782a",
    topbar: "rgba(255,255,255,.92)", topbarBorder: "#e6e1d6", inputBg: "#ffffff", inputBorder: "#ddd7cb",
    inputBg2: "#f8f6f1", overlay: "rgba(28,26,23,.42)",
    cardGrad: "none", kpiGrad: "linear-gradient(160deg,rgba(226,88,11,.05),rgba(255,255,255,0) 55%)",
    heroGlow: "radial-gradient(60% 120% at 88% -10%, rgba(226,88,11,.08), transparent 60%)",
    mapDot: "rgba(120,110,95,.35)", mapCity: "#e2580b",
    shadow: "0 1px 2px rgba(40,30,15,.06)", shadowMd: "0 6px 24px rgba(40,30,15,.08)",
    shadowLg: "0 24px 60px rgba(40,30,15,.16)", glow: "0 0 0 1px rgba(226,88,11,.25), 0 6px 22px rgba(226,88,11,.14)",
    scheme: "light",
  },
  dark: {
    ...SHAPE,
    bg: "#12110f", bgTint: "#161512", surface: "#1b1a17", surface2: "#171613", surface3: "#22201c",
    border: "#2b2925", borderStrong: "#3a3731", borderSoft: "#23211d",
    text: "#f2efe9", dim: "#a59e91", faint: "#6f695f",
    accent: "#fb8a3c", accent2: "#f5a524", accentText: "#1a0d02",
    accentRgb: "251 138 60", accent2Rgb: "245 165 36", onlineRgb: "74 222 128",
    accentSoft: "rgba(251,138,60,.12)", accentSoft2: "rgba(245,165,36,.10)", accentLine: "rgba(251,138,60,.35)",
    accentGrad: "linear-gradient(135deg,#ffa25c 0%,#fb8a3c 55%,#f0702a 100%)",
    online: "#4ade80", offline: "#6f695f", error: "#f5a524", warn: "#f5a524", danger: "#f26b5b",
    onlineBg: "rgba(74,222,128,.12)", offlineBg: "rgba(111,105,95,.18)", errorBg: "rgba(245,165,36,.13)",
    dangerBg: "rgba(242,107,91,.13)", infoBg: "rgba(96,165,250,.12)", info: "#60a5fa",
    sidebar: "#0f0e0c", sidebar2: "#0c0b0a", sidebarBorder: "#262420", sidebarBrand: "#faf8f4",
    sidebarItem: "#a59e91", sidebarMuted: "#6f695f", sidebarHover: "rgba(255,255,255,.05)",
    sidebarActiveBg: "rgba(251,138,60,.15)", sidebarActiveBar: "#fb8a3c",
    topbar: "rgba(23,22,19,.9)", topbarBorder: "#262420", inputBg: "#171613", inputBorder: "#33302b",
    inputBg2: "#1b1a17", overlay: "rgba(6,5,4,.7)",
    cardGrad: "none", kpiGrad: "linear-gradient(160deg,rgba(251,138,60,.06),rgba(255,255,255,0) 55%)",
    heroGlow: "radial-gradient(60% 120% at 88% -10%, rgba(251,138,60,.10), transparent 60%)",
    mapDot: "rgba(160,150,135,.4)", mapCity: "#fb8a3c",
    shadow: "0 1px 2px rgba(0,0,0,.4)", shadowMd: "0 8px 28px rgba(0,0,0,.4)",
    shadowLg: "0 28px 64px rgba(0,0,0,.6)", glow: "0 0 0 1px rgba(251,138,60,.28), 0 8px 26px rgba(251,138,60,.16)",
    scheme: "dark",
  },
};

// Kamera "feed" o'rni uchun gradient placeholderlar (iliq grafit).
export const FEEDS = [
  "radial-gradient(130% 95% at 50% 118%, #2f2a24 0%, #15130f 58%, #0b0a08 100%)",
  "radial-gradient(70% 60% at 72% 26%, rgba(190,175,150,.18), rgba(16,14,12,0) 55%), linear-gradient(180deg, #221e19, #0b0a08)",
  "linear-gradient(180deg, #17140f 0%, #2a241d 52%, #0c0a08 100%)",
  "radial-gradient(95% 75% at 50% -5%, #2c2721, #0e0c0a 72%)",
  "radial-gradient(48% 42% at 30% 34%, rgba(170,150,120,.2), transparent 58%), radial-gradient(48% 40% at 76% 70%, rgba(150,120,90,.14), transparent 55%), #0e0c0a",
  "linear-gradient(180deg, #16130f, #221d17 60%, #0c0a08)",
];
export const feedFor = (i) => FEEDS[((i % FEEDS.length) + FEEDS.length) % FEEDS.length];

// "no_stream" — ALOHIDA KATEGORIYA: qurilma tarmoqda bor-u video bermayapti.
export const STATUS_LABEL = {
  online: "Online", offline: "Offline", error: "Xatolik", no_stream: "Oqim yo'q",
};
export const STATUS_SHORT = {
  online: "LIVE", offline: "OFF", error: "ERR", no_stream: "OQIM?",
};

const ThemeCtx = createContext(null);
const KEY = "vz_theme";

function applyTheme(name) {
  const tokens = THEMES[name] || THEMES.light;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(tokens)) root.style.setProperty(`--${k}`, v);
  root.style.colorScheme = tokens.scheme;
  root.setAttribute("data-theme", name);
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => localStorage.getItem(KEY) || "light");

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);

  const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return (
    <ThemeCtx.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeCtx.Provider>
  );
}

export const useTheme = () => useContext(ThemeCtx);
