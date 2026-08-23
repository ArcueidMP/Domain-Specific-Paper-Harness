type RunStatusBadgeProps = {
  status: string;
};

export function RunStatusBadge({ status }: RunStatusBadgeProps) {
  const normalized = status.toLowerCase();
  const unavailable = [
    "analysis_unavailable",
    "related_work_unavailable",
    "comparison_unavailable",
    "limited_comparability",
    "insufficient_data",
    "limited_data",
  ].includes(normalized);
  const style = unavailable
    ? "unavailable"
    : ["complete", "partial", "failed", "running", "no_update", "available"].includes(
          normalized,
        )
      ? normalized
      : "neutral";

  return <span className={`status-badge ${style}`}>{status.replaceAll("_", " ")}</span>;
}
