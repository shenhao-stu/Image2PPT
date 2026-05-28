import type { Job } from "../api/client";
import { api } from "../api/client";
import { fmtETA, fmtTime, kindFromSource, MODE_LABEL } from "../utils/format";
import { Icon } from "./icons";
import StatusPill from "./StatusPill";

function FileGlyph({ kind }: { kind: "pdf" | "img" | "zip" }) {
  if (kind === "pdf") return <Icon.Pdf />;
  if (kind === "zip") return <Icon.Zip />;
  return <Icon.Image />;
}

export default function JobRow({
  job,
  onDelete,
  onCancel,
  onRetry,
  showDuration,
}: {
  job: Job;
  onDelete: (id: string) => void;
  onCancel?: (id: string) => void;
  onRetry?: (id: string) => void;
  showDuration: boolean;
}) {
  const kind = kindFromSource(job.source_kind);

  const queueText =
    job.status === "queued"
      ? job.queue_position > 0
        ? `第 ${job.queue_position} 位`
        : "下一个"
      : job.status === "running"
      ? `还需 ${fmtETA(job.eta_seconds)}`
      : showDuration
      ? `用时 ${fmtETA(job.duration_seconds)}`
      : "—";

  const queueLbl =
    job.status === "queued"
      ? `预计 ${fmtETA(job.eta_seconds)}`
      : job.status === "running"
      ? "剩余时间"
      : showDuration
      ? "总耗时"
      : "";

  return (
    <div className={`job-row ${job.status === "done" ? "has-download" : ""}`}>
      <div className={`file-icon ${kind}`}>
        <FileGlyph kind={kind} />
      </div>

      <div style={{ minWidth: 0 }}>
        <div className="filename" title={job.source_filename}>
          {job.source_filename}
        </div>
        <div className="submeta">
          <span>{job.page_count} 页</span>
          <span>·</span>
          <span>{fmtTime(job.created_at)}</span>
          {job.error_msg && (
            <>
              <span>·</span>
              <span className="err" title={job.error_msg}>
                {job.error_msg.length > 36 ? job.error_msg.slice(0, 36) + "…" : job.error_msg}
              </span>
            </>
          )}
        </div>
      </div>

      <div className="col-mode">
        <span className="mode-tag">{MODE_LABEL[job.mode] || job.mode}</span>
      </div>

      <div>
        <StatusPill status={job.status} />
      </div>

      <div className="prog-cell col-progress">
        <div className={`progress ${job.status}`}>
          <div className="bar" style={{ width: `${job.progress_pct}%` }} />
        </div>
        <div className="info">
          <span>{job.current_page > 0 ? `${job.current_page}/${job.page_count}` : "—"}</span>
          <span>{job.progress_pct}%</span>
        </div>
      </div>

      <div className="eta col-eta">
        {queueLbl && <span className="lbl">{queueLbl}</span>}
        <span>{queueText}</span>
      </div>

      <div className="row-actions">
        {job.status === "done" && (
          <a href={api.downloadUrl(job.id)}>
            <button className="btn primary sm" title="下载 PPTX">
              <Icon.Download /> 下载
            </button>
          </a>
        )}
        {(job.status === "failed" || job.status === "canceled") && onRetry && (
          <button
            className="btn sm"
            onClick={() => onRetry(job.id)}
            title="使用原文件重新转换"
          >
            <Icon.Refresh /> 重试
          </button>
        )}
        {(job.status === "running" || job.status === "queued") && onCancel && (
          <button
            className="btn sm ghost danger"
            onClick={() => {
              if (confirm(`确定中断「${job.source_filename}」吗？`)) onCancel(job.id);
            }}
            title="中断任务"
          >
            <Icon.Stop /> 中断
          </button>
        )}
        <button
          className="btn icon ghost danger"
          disabled={job.status === "running" || job.status === "queued"}
          onClick={() => {
            if (confirm(`确定删除「${job.source_filename}」吗？`)) onDelete(job.id);
          }}
          title="删除"
        >
          <Icon.Trash />
        </button>
      </div>
    </div>
  );
}
