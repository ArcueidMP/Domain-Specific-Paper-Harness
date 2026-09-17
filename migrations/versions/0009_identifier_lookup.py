"""Index normalized scholarly identifiers without changing source identifiers.

Revision ID: 0009_identifier_lookup
Revises: 0008_enrichment_failures
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision: str = "0009_identifier_lookup"
down_revision: str | None = "0008_enrichment_failures"
branch_labels = None
depends_on = None

_BATCH_SIZE = 500


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "Identifier lookup migration requires an online database connection for "
            "Python casefold backfill; offline SQL generation is not supported."
        )

    op.add_column("external_paper_identifiers", sa.Column("normalized_type", sa.Text()))
    op.add_column("external_paper_identifiers", sa.Column("normalized_value", sa.Text()))

    connection = op.get_bind()
    cursor = None
    while True:
        predicate = (
            " WHERE (external_paper_id, identifier_type) > (:last_paper_id, :last_type)"
            if cursor is not None
            else ""
        )
        parameters = {"batch_size": _BATCH_SIZE}
        if cursor is not None:
            parameters.update(last_paper_id=cursor[0], last_type=cursor[1])
        rows = connection.execute(
            sa.text(
                "SELECT external_paper_id, identifier_type, identifier_value "
                f"FROM external_paper_identifiers{predicate} "
                "ORDER BY external_paper_id, identifier_type LIMIT :batch_size"
            ),
            parameters,
        ).all()
        if not rows:
            break

        updates = []
        for paper_id, identifier_type, identifier_value in rows:
            normalized_type = identifier_type.casefold()
            normalized_value = (
                identifier_value.casefold()
                if normalized_type in {"arxiv", "doi"}
                else identifier_value
            )
            updates.append(
                {
                    "paper_id": paper_id,
                    "identifier_type": identifier_type,
                    "normalized_type": normalized_type,
                    "normalized_value": normalized_value,
                }
            )
        connection.execute(
            sa.text(
                "UPDATE external_paper_identifiers "
                "SET normalized_type = :normalized_type, normalized_value = :normalized_value "
                "WHERE external_paper_id = :paper_id AND identifier_type = :identifier_type"
            ),
            updates,
        )
        cursor = (rows[-1][0], rows[-1][1])

    collision = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM external_paper_identifiers "
            "GROUP BY normalized_type, normalized_value HAVING count(*) > 1)"
        )
    )
    if collision:
        raise RuntimeError(
            "Identifier lookup migration found normalized identifier collisions; "
            "resolve source ownership before retrying. No source identifiers were changed."
        )

    op.alter_column("external_paper_identifiers", "normalized_type", nullable=False)
    op.alter_column("external_paper_identifiers", "normalized_value", nullable=False)
    op.create_unique_constraint(
        "uq_external_paper_identifiers_normalized",
        "external_paper_identifiers",
        ["normalized_type", "normalized_value"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_external_paper_identifiers_normalized", "external_paper_identifiers", type_="unique"
    )
    op.drop_column("external_paper_identifiers", "normalized_value")
    op.drop_column("external_paper_identifiers", "normalized_type")
