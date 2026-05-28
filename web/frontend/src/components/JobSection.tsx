import type { Job } from "../api/client";
import JobRow from "./JobRow";

export default function JobSection({
  title,
  jobs,
  onDelete,
  onCancel,
  onRetry,
  showDuration,
  action,
  emptyHint,
}: {
  title: string;
  jobs: Job[];
  onDelete: (id: string) => void;
  onCancel?: (id: string) => void;
  onRetry?: (id: string) => void;
  showDuration: boolean;
  action?: React.ReactNode;
  emptyHint?: string;
}) {
  return (
    <div className="card jobs-card">
      <div className="jobs-head">
        <h3>
          {title}
          <span className="count">{jobs.length}</span>
        </h3>
        <div className="right">{action}</div>
      </div>
      {jobs.length === 0 ? (
        <div className="empty">
          {emptyHint || "暂无任务"}
          <span className="mono">// no jobs</span>
        </div>
      ) : (
        <div className="job-rows">
          {jobs.map((j) => (
            <JobRow
              key={j.id}
              job={j}
              onDelete={onDelete}
              onCancel={onCancel}
              onRetry={onRetry}
              showDuration={showDuration}
            />
          ))}
        </div>
      )}
    </div>
  );
}
