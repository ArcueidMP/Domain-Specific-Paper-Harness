import {
  Link,
  NavLink,
  createPath,
  parsePath,
  useSearchParams,
} from "react-router-dom";

import type { LinkProps, NavLinkProps, To } from "react-router-dom";
import { defaultTopicSlug } from "../lib/topic";

function withTopic(to: To, topic: string): To {
  if (typeof to === "string") {
    const destination = parsePath(to);
    const searchParams = new URLSearchParams(destination.search);
    searchParams.set("topic", topic);
    return createPath({ ...destination, search: `?${searchParams.toString()}` });
  }

  const searchParams = new URLSearchParams(to.search);
  searchParams.set("topic", topic);
  return { ...to, search: `?${searchParams.toString()}` };
}

function useTopicDestination(to: To): To {
  const [searchParams] = useSearchParams();
  const topic = searchParams.get("topic") ?? defaultTopicSlug;
  return withTopic(to, topic);
}

export function TopicLink({ to, ...props }: LinkProps) {
  return <Link {...props} to={useTopicDestination(to)} />;
}

export function TopicNavLink({ to, ...props }: NavLinkProps) {
  return <NavLink {...props} to={useTopicDestination(to)} />;
}
