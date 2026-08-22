import { TopicLink } from "../components/TopicLink";

export function NotFoundPage() {
  return (
    <section className="not-found page-section">
      <p className="eyebrow">404 / Not found</p>
      <h1>This research path does not exist.</h1>
      <p>The page may have moved, or the address may be incomplete.</p>
      <TopicLink className="primary-button" to="/">
        Return to dashboard
      </TopicLink>
    </section>
  );
}
