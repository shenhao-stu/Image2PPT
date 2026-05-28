// Tiny i18n shim. No external library; just a flat key→string map per
// locale + a hook to subscribe to the global locale flag.

import { useSyncExternalStore } from "react";

export type Locale = "zh" | "en";

const STORAGE_KEY = "recta.locale";

// Pull initial locale from localStorage, fall back to browser language.
function readInitial(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "en" || saved === "zh") return saved;
  const nav = (navigator.language || "").toLowerCase();
  return nav.startsWith("zh") ? "zh" : "en";
}

let _locale: Locale = readInitial();
const subs = new Set<() => void>();

export function getLocale(): Locale {
  return _locale;
}

export function setLocale(l: Locale): void {
  if (l === _locale) return;
  _locale = l;
  localStorage.setItem(STORAGE_KEY, l);
  document.documentElement.setAttribute("data-lang", l);
  subs.forEach((cb) => cb());
}

// Apply on first paint.
document.documentElement.setAttribute("data-lang", _locale);

export function useLocale(): Locale {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
    () => _locale,
    () => _locale,
  );
}

// Theme — companion to i18n; same pattern.
export type Theme = "dark" | "light";
const THEME_KEY = "recta.theme";

function readTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return "dark";
}

let _theme: Theme = readTheme();
const themeSubs = new Set<() => void>();

export function getTheme(): Theme {
  return _theme;
}

export function setTheme(t: Theme): void {
  if (t === _theme) return;
  _theme = t;
  localStorage.setItem(THEME_KEY, t);
  document.documentElement.setAttribute("data-theme", t);
  themeSubs.forEach((cb) => cb());
}

document.documentElement.setAttribute("data-theme", _theme);

export function useTheme(): Theme {
  return useSyncExternalStore(
    (cb) => {
      themeSubs.add(cb);
      return () => themeSubs.delete(cb);
    },
    () => _theme,
    () => _theme,
  );
}

// Accent palette — getoken-style swatch picker. Five named palettes,
// each backed by CSS variables under :root[data-accent="<name>"].
export type Accent = "rose" | "blue" | "amber" | "violet" | "emerald";
export const ACCENT_ORDER: Accent[] = ["rose", "blue", "amber", "violet", "emerald"];
const ACCENT_KEY = "recta.accent";

function readAccent(): Accent {
  const saved = localStorage.getItem(ACCENT_KEY);
  if (saved && (ACCENT_ORDER as string[]).includes(saved)) return saved as Accent;
  return "rose";
}

let _accent: Accent = readAccent();
const accentSubs = new Set<() => void>();

export function getAccent(): Accent {
  return _accent;
}

export function setAccent(a: Accent): void {
  if (a === _accent) return;
  _accent = a;
  localStorage.setItem(ACCENT_KEY, a);
  document.documentElement.setAttribute("data-accent", a);
  accentSubs.forEach((cb) => cb());
}

document.documentElement.setAttribute("data-accent", _accent);

export function useAccent(): Accent {
  return useSyncExternalStore(
    (cb) => {
      accentSubs.add(cb);
      return () => accentSubs.delete(cb);
    },
    () => _accent,
    () => _accent,
  );
}

// Human labels for the swatch tooltip.
export const ACCENT_LABEL: Record<Accent, string> = {
  rose: "Rose",
  blue: "Blue",
  amber: "Amber",
  violet: "Violet",
  emerald: "Emerald",
};

// Swatch dot color (for the picker chip itself — independent of theme).
export const ACCENT_SWATCH: Record<Accent, string> = {
  rose: "#fb7185",
  blue: "#3b82f6",
  amber: "#f59e0b",
  violet: "#8b5cf6",
  emerald: "#10b981",
};

// String table. Keep it shallow; add keys as the UI grows. Falls back
// to the key itself on miss, so missing translations are visible (not
// silently empty).
const STR: Record<Locale, Record<string, string>> = {
  zh: {
    "brand.tagline": "把图片或 PDF 转为可编辑 PowerPoint。",
    "nav.workspace": "工作区",
    "nav.admin": "管理",
    "nav.new": "新建任务",
    "nav.active": "正在进行任务",
    "nav.history": "历史任务",
    "nav.system": "系统",
    "role.admin": "管理员",
    "role.member": "成员",
    "repo.uptodate": "已是最新",
    "repo.updating": "更新中",
    "repo.behind": "落后 {n}",
    "login.username": "用户名",
    "login.password": "密码",
    "login.submit": "登录",
    "login.submitting": "登录中…",
    "login.see_source": "查看源码 · 报告问题",
    "login.pow.title": "人机验证",
    "login.pow.computing": "计算中…",
    "login.pow.ready": "已通过",
    "login.pow.hint": "提交前需要计算一次轻量级 PoW，约 1-2 秒。",
    "topbar.toggleLang": "EN",
    "topbar.toggleTheme.toLight": "切换浅色",
    "topbar.toggleTheme.toDark": "切换深色",
    "topbar.logout": "退出登录",
  },
  en: {
    "brand.tagline": "Rebuild slide screenshots or PDFs as editable PowerPoint.",
    "nav.workspace": "Workspace",
    "nav.admin": "Admin",
    "nav.new": "New job",
    "nav.active": "In progress",
    "nav.history": "History",
    "nav.system": "System",
    "role.admin": "Admin",
    "role.member": "Member",
    "repo.uptodate": "Up to date",
    "repo.updating": "Updating",
    "repo.behind": "{n} commits behind",
    "login.username": "Username",
    "login.password": "Password",
    "login.submit": "Sign in",
    "login.submitting": "Signing in…",
    "login.see_source": "View source · Report issue",
    "login.pow.title": "Anti-bot check",
    "login.pow.computing": "Computing…",
    "login.pow.ready": "Verified",
    "login.pow.hint": "A light proof-of-work takes ~1-2 s before sign-in.",
    "topbar.toggleLang": "中",
    "topbar.toggleTheme.toLight": "Switch to light",
    "topbar.toggleTheme.toDark": "Switch to dark",
    "topbar.logout": "Sign out",
  },
};

export function t(key: string, vars?: Record<string, string | number>): string {
  const table = STR[_locale] || STR.en;
  let s = table[key] ?? STR.en[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.split(`{${k}}`).join(String(v));
    }
  }
  return s;
}
