/** TopBar — sticky toolbar in the top-right corner.
 *  Mirrors getoken.tech's pattern: EN + accent-color swatch + theme.
 */
import { Icon } from "./icons";
import AccentPicker from "./AccentPicker";
import { getLocale, setLocale, getTheme, setTheme, t, useLocale, useTheme } from "../i18n";

export default function TopBar({ extra }: { extra?: React.ReactNode }) {
  // Subscribe so this re-renders when the user flips locale / theme.
  useLocale();
  useTheme();

  const otherLang = getLocale() === "zh" ? "en" : "zh";
  const otherTheme = getTheme() === "dark" ? "light" : "dark";

  return (
    <div className="topbar">
      <button
        className="btn icon ghost"
        onClick={() => setLocale(otherLang)}
        title={otherLang === "en" ? "Switch to English" : "切换到中文"}
        aria-label="toggle language"
      >
        <span className="topbar-lang">{t("topbar.toggleLang")}</span>
      </button>
      <AccentPicker />
      <button
        className="btn icon ghost"
        onClick={() => setTheme(otherTheme)}
        title={otherTheme === "light" ? t("topbar.toggleTheme.toLight") : t("topbar.toggleTheme.toDark")}
        aria-label="toggle theme"
      >
        {getTheme() === "dark" ? <Icon.Sun /> : <Icon.Moon />}
      </button>
      {extra}
    </div>
  );
}
