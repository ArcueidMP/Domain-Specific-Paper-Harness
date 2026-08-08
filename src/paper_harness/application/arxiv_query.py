"""Deterministic arXiv query construction owned by the application layer."""

from paper_harness.domain.models import TopicConfig


def build_arxiv_query(topic: TopicConfig) -> str:
    categories = " OR ".join(f"cat:{category}" for category in topic.categories)
    included = " OR ".join(f'all:"{term}"' for term in topic.include_terms)
    query = f"({categories}) AND ({included})"
    if topic.exclude_terms:
        excluded = " OR ".join(f'all:"{term}"' for term in topic.exclude_terms)
        query = f"{query} ANDNOT ({excluded})"
    return query
