// App shell after login: sidebar + page head + paged content.
// Each page is rendered inline (the design's page split lives here).
import { useCallback, useEffect, useState } from "react";
import { api, type Job, type Me, type VersionInfo } from "../api/client";
import { connectWS } from "../api/ws";
import JobSection from "../components/JobSection";
import PageHead from "../components/PageHead";
import Sidebar, { type Page } from "../components/Sidebar";
import TopBar from "../components/TopBar";
import UpdateBanner from "../components/UpdateBanner";
import UploadCard from "../components/UploadCard";
import { Icon } from "../components/icons";
import { t, useLocale } from "../i18n";
import SystemPage from "./SystemPage";

function pageTitles(): Record<Page, [string, string]> {
  return {
    new: [t("nav.new"), "/ workspace / new"],
    active: [t("nav.active"), "/ workspace / active"],
    history: [t("nav.history"), "/ workspace / history"],
    system: [t("nav.system"), "/ admin / system"],
  };
}

export default function Dashboard({
  me,
  onLogout,
}: {
  me: Me;
  onLogout: () => void;
}) {
  useLocale();  // re-render on language change
  const [jobs, setJobs] = useState<Job[]>([]);
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [page, setPage] = useState<Page>("new");

  const reloadJobs = useCallback(async () => {
    try {
      setJobs(await api.listJobs());
    } catch {}
  }, []);

  const reloadVersion = useCallback(async () => {
    try {
      setVersion(await api.version());
    } catch {}
  }, []);

  useEffect(() => {
    reloadJobs();
    reloadVersion();
    const close = connectWS((msg) => {
      if (msg.type === "job") {
        setJobs((prev) =>
          prev.map((j) =>
            j.id === msg.id
              ? {
                  ...j,
                  status: msg.status as Job["status"],
                  progress_pct: msg.progress_pct,
                  current_page: msg.current_page,
                  page_count: msg.page_count,
                }
              : j
          )
        );
        if (msg.status === "done" || msg.status === "failed" || msg.status === "running") {
          reloadJobs();
        }
      } else if (msg.type === "system") {
        setVersion((v) =>
          v
            ? {
                ...v,
                commit: msg.commit,
                short_commit: msg.short_commit,
                behind: msg.behind,
                ahead: msg.ahead,
                updating: msg.updating,
              }
            : v
        );
      }
    });
    const t = window.setInterval(reloadJobs, 15000);
    return () => {
      close();
      window.clearInterval(t);
    };
  }, [reloadJobs, reloadVersion]);

  const onDelete = async (id: string) => {
    await api.deleteJob(id);
    reloadJobs();
  };

  const onCancel = async (id: string) => {
    try {
      await api.cancelJob(id);
    } catch (e) {
      alert((e as Error).message);
    }
    reloadJobs();
  };

  const onRetry = async (id: string) => {
    try {
      await api.retryJob(id);
      // Jump to the active list so the user sees the requeued job.
      setPage("active");
    } catch (e) {
      alert((e as Error).message);
    }
    reloadJobs();
  };

  const onBulkDelete = async (ids: string[]) => {
    // One round-trip so the server can finish disk cleanup before
    // dropping each DB row. The server returns per-id status; we only
    // need to surface failures.
    try {
      const res = await api.bulkDeleteJobs(ids);
      if (res.skipped.length > 0) {
        const lines = res.skipped
          .slice(0, 5)
          .map((s) => `• ${s.id.slice(0, 8)}: ${s.reason}`)
          .join("\n");
        alert(
          `${res.deleted.length}/${ids.length} 条删除成功，${res.skipped.length} 条跳过。\n` +
            lines +
            (res.skipped.length > 5 ? `\n…还有 ${res.skipped.length - 5} 条` : ""),
        );
      }
    } catch (e) {
      alert((e as Error).message);
    }
    reloadJobs();
  };

  const triggerUpdate = async () => {
    try {
      await api.triggerUpdate();
      reloadVersion();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const history = jobs.filter((j) => !["queued", "running"].includes(j.status));

  const clearHistory = () => {
    if (history.length === 0) return;
    if (confirm(`删除 ${history.length} 条历史记录？对应的上传文件和生成产物会一并清理。`)) {
      onBulkDelete(history.map((j) => j.id));
    }
  };

  let pageContent: React.ReactNode = null;
  if (page === "new") {
    pageContent = (
      <main className="main">
        {version && version.behind > 0 && !version.updating && me.is_admin && (
          <UpdateBanner version={version} onUpdate={triggerUpdate} />
        )}
        {version && version.updating && (
          <UpdateBanner version={version} onUpdate={triggerUpdate} />
        )}
        <UploadCard onCreated={() => { reloadJobs(); setPage("active"); }} />
      </main>
    );
  } else if (page === "active") {
    pageContent = (
      <main className="main">
        <JobSection
          title="正在进行"
          jobs={active}
          onDelete={onDelete}
          onCancel={onCancel}
          showDuration={false}
          emptyHint="队列为空，上传文件以开始"
        />
      </main>
    );
  } else if (page === "history") {
    pageContent = (
      <main className="main">
        <JobSection
          title="历史任务"
          jobs={history}
          onDelete={onDelete}
          onRetry={onRetry}
          showDuration={true}
          emptyHint="还没有完成过的任务"
          action={
            history.length > 0 && (
              <button className="btn sm ghost danger" onClick={clearHistory}>
                <Icon.Trash /> 清空已完成
              </button>
            )
          }
        />
      </main>
    );
  } else if (page === "system") {
    pageContent = (
      <SystemPage
        version={version}
        onUpdate={triggerUpdate}
        onCheckUpdate={reloadVersion}
        onVersionChange={(patch) =>
          setVersion((v) => (v ? { ...v, ...patch } : v))
        }
      />
    );
  }

  const [title, crumb] = pageTitles()[page];
  const headActions =
    page === "active" || page === "history" ? (
      <button className="btn primary sm" onClick={() => setPage("new")}>
        <Icon.Plus /> {t("nav.new")}
      </button>
    ) : null;

  return (
    <div className="app">
      <Sidebar
        page={page}
        setPage={setPage}
        me={me}
        version={version}
        jobs={jobs}
        onLogout={onLogout}
      />
      <div className="app-main">
        <PageHead title={title} crumb={crumb} actions={
          <>
            {headActions}
            <TopBar />
          </>
        } />
        {pageContent}
      </div>
    </div>
  );
}
