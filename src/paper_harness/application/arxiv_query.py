"""Deterministic arXiv query and local topic matching owned by the application."""

import unicodedata

from paper_harness.domain.models import TopicConfig
from paper_harness.ports.arxiv import ArxivPaperRecord


def matches_arxiv_topic(topic: TopicConfig, record: ArxivPaperRecord) -> bool:
    def normalized(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    content = normalized(f"{record.title} {record.abstract}")
    return (
        bool(set(topic.categories).intersection(record.categories))
        and any(normalized(term) in content for term in topic.include_terms)
        and not any(normalized(term) in content for term in topic.exclude_terms)
    )


def build_arxiv_query(topic: TopicConfig) -> str:
    categories = " OR ".join(f"cat:{category}" for category in topic.categories)
    included = " OR ".join(f'all:"{term}"' for term in topic.include_terms)
    query = f"({categories}) AND ({included})"
    if topic.exclude_terms:
        excluded = " OR ".join(f'all:"{term}"' for term in topic.exclude_terms)
        query = f"{query} ANDNOT ({excluded})"
    return query
