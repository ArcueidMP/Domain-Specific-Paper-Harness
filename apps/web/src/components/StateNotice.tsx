type StateNoticeProps =
  | {
      kind: "loading";
      title?: string;
      detail?: string;
      onRetry?: never;
    }
  | {
      kind: "empty";
      title: string;
      detail: string;
      onRetry?: never;
    }
  | {
      kind: "error";
      title?: string;
      detail: string;
      onRetry: () => void;
    };

export function StateNotice(props: StateNoticeProps) {
  const title = props.title ?? (props.kind === "loading" ? "Loading research data" : "Unable to load data");

  if (props.kind === "loading") {
    return (
      <div className="state-notice" role="status" aria-live="polite">
        <span className="loading-orbit" aria-hidden="true" />
        <div>
          <strong>{title}</strong>
          <p>{props.detail ?? "Reading the latest persisted results…"}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={`state-notice ${props.kind}`} role={props.kind === "error" ? "alert" : "status"}>
      <span className="state-icon" aria-hidden="true">
        {props.kind === "empty" ? "○" : "!"}
      </span>
      <div>
        <strong>{title}</strong>
        <p>{props.detail}</p>
        {props.kind === "error" ? (
          <button className="text-button" type="button" onClick={props.onRetry}>
            Try again
          </button>
        ) : null}
      </div>
    </div>
  );
}
