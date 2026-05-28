import { useEffect, useRef, useState } from "react";
import { api, auth, type Me } from "../api/client";
import { Icon } from "../components/icons";
import TopBar from "../components/TopBar";
import { t, useLocale } from "../i18n";
import { solvePow } from "../utils/pow";

const REPO_URL = "https://github.com/shenhao-stu/Image2PPT";

type PowState =
  | { phase: "idle" }
  | { phase: "fetching" }
  | { phase: "solving"; difficulty: number; tries: number }
  | { phase: "ready"; challenge: string; nonce: string; difficulty: number }
  | { phase: "error"; msg: string };

export default function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  useLocale();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pow, setPow] = useState<PowState>({ phase: "idle" });
  const requested = useRef(false);

  // Kick off PoW the moment the page mounts. Browser solves it while the
  // user is still typing the password, so submit is instantaneous.
  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    (async () => {
      try {
        setPow({ phase: "fetching" });
        const c = await api.powChallenge();
        setPow({ phase: "solving", difficulty: c.difficulty, tries: 0 });
        const nonce = await solvePow(c.challenge, c.difficulty, (tries) => {
          setPow({ phase: "solving", difficulty: c.difficulty, tries });
        });
        setPow({ phase: "ready", challenge: c.challenge, nonce, difficulty: c.difficulty });
      } catch (e) {
        setPow({ phase: "error", msg: (e as Error).message });
      }
    })();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    if (pow.phase !== "ready") {
      setErr("Anti-bot check is still running — please wait a second.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await api.login(username, password, pow.challenge, pow.nonce);
      auth.setToken(res.access_token);
      onLogin({ id: 0, username: res.username, is_admin: res.is_admin });
    } catch (e) {
      setErr((e as Error).message);
      // PoW is single-use; refetch on any failure so next submit succeeds.
      requested.current = false;
      setPow({ phase: "idle" });
      setTimeout(() => {
        requested.current = true;
        (async () => {
          setPow({ phase: "fetching" });
          const c = await api.powChallenge();
          setPow({ phase: "solving", difficulty: c.difficulty, tries: 0 });
          const nonce = await solvePow(c.challenge, c.difficulty);
          setPow({ phase: "ready", challenge: c.challenge, nonce, difficulty: c.difficulty });
        })();
      }, 0);
    } finally {
      setBusy(false);
    }
  };

  const powLabel = (() => {
    switch (pow.phase) {
      case "idle":
      case "fetching":
        return t("login.pow.computing");
      case "solving":
        return `${t("login.pow.computing")}  ·  ${pow.tries.toLocaleString()}`;
      case "ready":
        return t("login.pow.ready");
      case "error":
        return pow.msg;
    }
  })();

  return (
    <div className="login-wrap">
      <div className="login-topbar">
        <TopBar />
      </div>

      <div className="login-card-wrap">
        <form className="login-card" onSubmit={submit}>
          <div className="login-brand-row">
            <h1 className="login-wordmark">
              recta<span className="brand-dot">.</span>
            </h1>
            <div className="login-sub">{t("brand.tagline")}</div>
          </div>

          <label className="field">
            <span>{t("login.username")}</span>
            <input
              className="input"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label className="field">
            <span>{t("login.password")}</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              placeholder="••••••••"
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          <div className={`login-pow login-pow-${pow.phase}`}>
            <Icon.Shield />
            <div className="login-pow-body">
              <div className="login-pow-title">{t("login.pow.title")}</div>
              <div className="login-pow-status">{powLabel}</div>
            </div>
          </div>

          {err && <div className="err">{err}</div>}

          <button
            className="btn primary login-btn"
            disabled={busy || !password || pow.phase !== "ready"}
          >
            {busy ? t("login.submitting") : t("login.submit")}
          </button>

          <a className="login-repo" href={REPO_URL} target="_blank" rel="noreferrer">
            <Icon.Github />
            <div className="info">
              <div className="login-repo-name">shenhao-stu/Image2PPT</div>
              <div className="login-repo-meta">{t("login.see_source")}</div>
            </div>
            <span className="login-repo-arrow">↗</span>
          </a>

          <div className="foot">
            <span>recta · web</span>
            <span>v0.1.0</span>
          </div>
        </form>
      </div>
    </div>
  );
}
