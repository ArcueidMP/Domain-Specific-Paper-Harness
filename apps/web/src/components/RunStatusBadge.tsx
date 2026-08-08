type RunStatusBadgeProps = {
  status: string;
};

export function RunStatusBadge({ status }: RunStatusBadgeProps) {
  const normalized = status.toLowerCase();
  const style = ["complete", "partial", "failed", "running"].includes(normalized)
    ? normalized
    : "neutral";

  return <span className={`status-badge ${style}`}>{status.replaceAll("_", " ")}</span>;
}
