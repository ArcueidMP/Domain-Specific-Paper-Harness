from __future__ import annotations

from typing import cast

from sqlalchemy import ForeignKeyConstraint, Table, UniqueConstraint

from paper_harness.adapters.postgres.models import (
    PaperSourceIdentityRow,
    PaperVersionRow,
    RunItemRow,
)


def test_version_identity_and_run_item_use_composite_paper_foreign_keys() -> None:
    version_table = cast(Table, PaperVersionRow.__table__)
    version_unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in version_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("id", "paper_id") in version_unique_columns

    for table in (
        cast(Table, PaperSourceIdentityRow.__table__),
        cast(Table, RunItemRow.__table__),
    ):
        composite_foreign_keys = {
            tuple(constraint.column_keys)
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert ("paper_version_id", "paper_id") in composite_foreign_keys
