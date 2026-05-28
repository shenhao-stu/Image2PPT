/** AccentPicker — palette swatch popover anchored to a topbar button.
 *  Five named accents, click to apply. Mirrors getoken.tech's "切换主题色"
 *  affordance with a slightly more explicit chip grid.
 */
import { useEffect, useRef, useState } from "react";
import {
  ACCENT_LABEL,
  ACCENT_ORDER,
  ACCENT_SWATCH,
  getAccent,
  setAccent,
  useAccent,
} from "../i18n";

export default function AccentPicker() {
  useAccent();
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement | null>(null);
  const btnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const current = getAccent();

  return (
    <div className="accent-picker">
      <button
        ref={btnRef}
        className="btn icon ghost accent-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label="切换主题色 / Switch accent"
        title="切换主题色"
      >
        <span
          className="accent-swatch"
          style={{ background: ACCENT_SWATCH[current] }}
        />
      </button>
      {open && (
        <div className="accent-pop" ref={popRef} role="menu">
          {ACCENT_ORDER.map((a) => (
            <button
              key={a}
              className={`accent-pop-item ${a === current ? "active" : ""}`}
              onClick={() => {
                setAccent(a);
                setOpen(false);
              }}
              title={ACCENT_LABEL[a]}
            >
              <span
                className="accent-swatch"
                style={{ background: ACCENT_SWATCH[a] }}
              />
              <span className="accent-pop-label">{ACCENT_LABEL[a]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
