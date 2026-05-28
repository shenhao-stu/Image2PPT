import type { Job, Me, VersionInfo } from "../api/client";
import { Icon } from "./icons";

export type Page = "new" | "active" | "history" | "system";

export default function Sidebar({
  page,
  setPage,
  me,
  version,
  jobs,
  onLogout,
}: {
  page: Page;
  setPage: (p: Page) => void;
  me: Me;
  version: VersionInfo | null;
  jobs: Job[];
  onLogout: () => void;
}) {
  const activeCount = jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  const historyCount = jobs.filter((j) => !["queued", "running"].includes(j.status)).length;

  type Item = { id: Page; label: string; icon: React.ReactNode; pill: string | number | null };

  const items: Item[] = [
    { id: "new", label: "新建任务", icon: <Icon.Plus />, pill: null },
    {
      id: "active",
      label: "正在进行任务",
      icon: <Icon.Lightning />,
      pill: activeCount > 0 ? activeCount : null,
    },
    {
      id: "history",
      label: "历史任务",
      icon: <Icon.Clock />,
      pill: historyCount > 0 ? historyCount : null,
    },
  ];

  const adminItems: Item[] = me.is_admin
    ? [
        {
          id: "system",
          label: "系统",
          icon: <Icon.Gear />,
          pill: version && version.behind > 0 ? "!" : null,
        },
      ]
    : [];

  const repoName =
    version?.remote_url.replace(/^https?:\/\/(www\.)?github\.com\//, "") || "shenhao-stu/Image2PPT";
  const repoStatusClass = version?.updating ? "updating" : (version?.behind ?? 0) > 0 ? "behind" : "up";
  const repoStatusText = version?.updating
    ? "更新中"
    : (version?.behind ?? 0) > 0
    ? `落后 ${version!.behind}`
    : "已是最新";

  return (
    <aside className="sidebar">
      <a className="brand" href="/" onClick={(e) => { e.preventDefault(); }}>
        <span className="brand-name">recta<span className="brand-dot">.</span></span>
      </a>

      <div className="nav-group">工作区</div>
      {items.map((it) => (
        <a
          key={it.id}
          className={`nav-item ${page === it.id ? "active" : ""}`}
          onClick={() => setPage(it.id)}
        >
          <span className="nav-icon">{it.icon}</span>
          <span>{it.label}</span>
          {it.pill != null && <span className="nav-pill">{it.pill}</span>}
        </a>
      ))}

      {adminItems.length > 0 && (
        <>
          <div className="nav-group">管理</div>
          {adminItems.map((it) => (
            <a
              key={it.id}
              className={`nav-item ${page === it.id ? "active" : ""}`}
              onClick={() => setPage(it.id)}
            >
              <span className="nav-icon">{it.icon}</span>
              <span>{it.label}</span>
              {it.pill != null && (
                <span
                  className="nav-pill"
                  style={{
                    background: "var(--warn-soft)",
                    color: "var(--warn)",
                    borderColor: "color-mix(in oklch, var(--warn) 30%, transparent)",
                  }}
                >
                  {it.pill}
                </span>
              )}
            </a>
          ))}
        </>
      )}

      <div className="nav-spacer" />

      {version && (
        <a
          className="repo-card"
          href={version.remote_url}
          target="_blank"
          rel="noreferrer"
          title={`分支：${version.branch}`}
        >
          <Icon.Github />
          <div className="info">
            <div className="repo-name">{repoName}</div>
            <div className="repo-meta">
              <span className={`repo-dot ${repoStatusClass}`} />
              <span className="mono">{version.short_commit}</span>
              <span style={{ color: "var(--faint)" }}>·</span>
              <span>{repoStatusText}</span>
            </div>
          </div>
        </a>
      )}

      <div className="user-card">
        <div className="avatar">{me.username.slice(0, 1).toUpperCase()}</div>
        <div className="info">
          <div className="name">{me.username}</div>
          <div className="role">{me.is_admin ? "管理员" : "成员"}</div>
        </div>
        <button className="btn icon ghost" onClick={onLogout} title="退出登录">
          <Icon.Logout />
        </button>
      </div>
    </aside>
  );
}
