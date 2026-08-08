const dateTimeFormatter = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
});

const dateFormatter = new Intl.DateTimeFormat("en", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "Not recorded";
  }

  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "Invalid date" : dateTimeFormatter.format(parsed);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "Date unavailable";
  }

  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "Invalid date" : dateFormatter.format(parsed);
}
