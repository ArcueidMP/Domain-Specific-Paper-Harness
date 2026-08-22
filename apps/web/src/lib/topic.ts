import { useSearchParams } from "react-router-dom";

export const defaultTopicSlug = "broad-llm-agents";

export function useTopicSlug(): string {
  const [searchParams] = useSearchParams();
  return searchParams.get("topic") ?? defaultTopicSlug;
}
