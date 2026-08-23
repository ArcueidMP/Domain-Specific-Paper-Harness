"""Least-privilege PostgreSQL bootstrap for the isolated public-demo schema."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from psycopg import Connection as PsycopgConnection
from psycopg import Error as PsycopgError
from psycopg import sql
from sqlalchemy import Connection, Engine, text

PRODUCTION_SCHEMA = "public"
DEMO_SCHEMA = "demo"
DEMO_SYNC_ROLE = "paper_harness_demo_sync"
DEMO_READ_ROLE = "paper_harness_demo_read"


class DemoSchemaBootstrapError(RuntimeError):
    """The database cannot enforce the required public-demo isolation boundary."""


@dataclass(frozen=True, slots=True)
class DemoSchemaBootstrapResult:
    schema: str
    sync_role: str
    read_role: str
    source_table_count: int
    readable_source_column_count: int
    demo_table_count: int


def bootstrap_demo_schema(
    owner_engine: Engine,
    sync_engine: Engine,
    *,
    sync_password: str,
    read_password: str,
    migrate: Callable[[], None],
    source_columns: Mapping[str, Sequence[str]],
) -> DemoSchemaBootstrapResult:
    """Create roles/schema, migrate the schema, and enforce exact grants.

    The owner connection is used only by this explicit operation. Runtime and
    synchronization processes receive separate, narrowly scoped credentials.
    """

    _validate_password(sync_password, name="DEMO_SYNC_DB_PASSWORD")
    _validate_password(read_password, name="DEMO_READ_DB_PASSWORD")
    normalized_columns = _normalize_source_columns(source_columns)

    with owner_engine.begin() as connection:
        _assert_bootstrap_authority(connection)
        _upsert_login_role(connection, DEMO_SYNC_ROLE, sync_password, read_only=False)
        _upsert_login_role(connection, DEMO_READ_ROLE, read_password, read_only=True)
        _prepare_schema(connection)

    migrate()

    with owner_engine.begin() as connection:
        _transfer_demo_object_ownership(connection)
        _apply_source_grants(connection, normalized_columns)
    with sync_engine.begin() as connection:
        _grant_existing_demo_read_access(connection)
        _apply_demo_default_privileges(connection)
    with owner_engine.begin() as connection:
        return _audit_permissions(connection, normalized_columns)


def _validate_password(value: str, *, name: str) -> None:
    if not value or "\x00" in value:
        raise DemoSchemaBootstrapError(f"{name} must be a non-empty PostgreSQL password")


def _normalize_source_columns(
    source_columns: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {"alembic_version": ("version_num",)}
    for table_name, column_names in source_columns.items():
        if not _is_identifier(table_name):
            raise DemoSchemaBootstrapError("demo source policy contains an invalid table name")
        columns = tuple(dict.fromkeys(column_names))
        if not columns or any(not _is_identifier(column) for column in columns):
            raise DemoSchemaBootstrapError(
                f"demo source policy for {table_name!r} contains invalid columns"
            )
        normalized[table_name] = columns
    return dict(sorted(normalized.items()))


def _is_identifier(value: str) -> bool:
    return (
        bool(value)
        and value[0].islower()
        and all(
            character.islower() or character.isdigit() or character == "_" for character in value
        )
    )


def _assert_bootstrap_authority(connection: Connection) -> None:
    can_create_database_objects, can_create_roles = connection.execute(
        text(
            "SELECT has_database_privilege(current_user, current_database(), 'CREATE'), "
            "rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user"
        )
    ).one()
    if not can_create_database_objects or not can_create_roles:
        raise DemoSchemaBootstrapError(
            "bootstrap requires a database owner with CREATE and CREATEROLE privileges"
        )
    vector_available = bool(
        connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        )
    )
    if not vector_available:
        raise DemoSchemaBootstrapError(
            "bootstrap requires the existing PostgreSQL vector extension"
        )


def _upsert_login_role(
    connection: Connection,
    role_name: str,
    password: str,
    *,
    read_only: bool,
) -> None:
    exists = bool(
        connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role_name},
        )
    )
    role = sql.Identifier(role_name)
    password_literal = sql.Literal(password)
    if exists:
        statement = sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(role, password_literal)
    else:
        statement = sql.SQL(
            "CREATE ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(role, password_literal)
    _execute_composed(connection, statement)
    setting = "on" if read_only else "off"
    _execute_composed(
        connection,
        sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}").format(
            role, sql.SQL(setting)
        ),
    )
    _execute_composed(
        connection,
        sql.SQL("ALTER ROLE {} SET search_path = {}, {}").format(
            role,
            sql.Identifier(DEMO_SCHEMA),
            sql.Identifier("pg_catalog"),
        ),
    )


def _prepare_schema(connection: Connection) -> None:
    sync_role = sql.Identifier(DEMO_SYNC_ROLE)
    read_role = sql.Identifier(DEMO_READ_ROLE)
    demo_schema = sql.Identifier(DEMO_SCHEMA)
    public_schema = sql.Identifier(PRODUCTION_SCHEMA)
    database_name = sql.Identifier(str(connection.scalar(text("SELECT current_database()"))))
    schema_owner = connection.scalar(
        text(
            "SELECT owner.rolname FROM pg_namespace AS namespace "
            "JOIN pg_roles AS owner ON owner.oid = namespace.nspowner "
            "WHERE namespace.nspname = :schema"
        ),
        {"schema": DEMO_SCHEMA},
    )
    if schema_owner is None:
        owner_role = sql.Identifier(str(connection.scalar(text("SELECT current_user"))))
        _execute_composed(
            connection,
            sql.SQL("GRANT {} TO {}").format(sync_role, owner_role),
        )
        _execute_composed(
            connection,
            sql.SQL("CREATE SCHEMA {}").format(demo_schema),
        )
        _execute_composed(
            connection,
            sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(demo_schema, sync_role),
        )
        _execute_composed(
            connection,
            sql.SQL("REVOKE {} FROM {}").format(sync_role, owner_role),
        )
    elif schema_owner != DEMO_SYNC_ROLE:
        _execute_composed(
            connection, sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(demo_schema, sync_role)
        )
    for role in (sync_role, read_role):
        _execute_composed(
            connection,
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database_name, role),
        )
        _execute_composed(
            connection,
            sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(public_schema, role),
        )
        _execute_composed(
            connection,
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(public_schema, role),
        )
    _execute_composed(
        connection,
        sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(database_name, sync_role),
    )


def _transfer_demo_object_ownership(connection: Connection) -> None:
    rows = connection.execute(
        text(
            "SELECT c.relkind, c.relname, owner.rolname FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "JOIN pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'S', 'v', 'm') "
            "ORDER BY c.relkind, c.relname"
        ),
        {"schema": DEMO_SCHEMA},
    )
    kinds = {
        "r": sql.SQL("TABLE"),
        "p": sql.SQL("TABLE"),
        "S": sql.SQL("SEQUENCE"),
        "v": sql.SQL("VIEW"),
        "m": sql.SQL("MATERIALIZED VIEW"),
    }
    for relation_kind, relation_name, owner_name in rows:
        if owner_name == DEMO_SYNC_ROLE:
            continue
        _execute_composed(
            connection,
            sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                kinds[str(relation_kind)],
                sql.Identifier(DEMO_SCHEMA),
                sql.Identifier(str(relation_name)),
                sql.Identifier(DEMO_SYNC_ROLE),
            ),
        )


def _apply_source_grants(
    connection: Connection, source_columns: Mapping[str, Sequence[str]]
) -> None:
    sync_role = sql.Identifier(DEMO_SYNC_ROLE)
    read_role = sql.Identifier(DEMO_READ_ROLE)
    public_schema = sql.Identifier(PRODUCTION_SCHEMA)
    for role in (sync_role, read_role):
        _execute_composed(
            connection,
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                public_schema, role
            ),
        )
        _execute_composed(
            connection,
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                public_schema, role
            ),
        )
    for table_name, column_names in source_columns.items():
        _execute_composed(
            connection,
            sql.SQL("GRANT SELECT ({}) ON TABLE {}.{} TO {}").format(
                sql.SQL(", ").join(sql.Identifier(column) for column in column_names),
                public_schema,
                sql.Identifier(table_name),
                sync_role,
            ),
        )


def _grant_existing_demo_read_access(connection: Connection) -> None:
    demo_schema = sql.Identifier(DEMO_SCHEMA)
    read_role = sql.Identifier(DEMO_READ_ROLE)
    _execute_composed(
        connection, sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(demo_schema, read_role)
    )
    _execute_composed(
        connection,
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(demo_schema, read_role),
    )
    _execute_composed(
        connection,
        sql.SQL(
            "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
            "ON ALL TABLES IN SCHEMA {} FROM {}"
        ).format(demo_schema, read_role),
    )


def _apply_demo_default_privileges(connection: Connection) -> None:
    _execute_composed(
        connection,
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(
            sql.Identifier(DEMO_SCHEMA), sql.Identifier(DEMO_READ_ROLE)
        ),
    )


def _audit_permissions(
    connection: Connection, source_columns: Mapping[str, Sequence[str]]
) -> DemoSchemaBootstrapResult:
    source_tables = tuple(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ),
            {"schema": PRODUCTION_SCHEMA},
        )
    )
    source_column_rows = tuple(
        (str(table_name), str(column_name))
        for table_name, column_name in connection.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :schema ORDER BY table_name, ordinal_position"
            ),
            {"schema": PRODUCTION_SCHEMA},
        )
    )
    demo_tables = tuple(
        str(value)
        for value in connection.scalars(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ),
            {"schema": DEMO_SCHEMA},
        )
    )
    demo_table_count = len(demo_tables)
    role_rows = {
        str(role_name): (
            bool(inherits),
            bool(superuser),
            bool(create_database),
            bool(create_role),
            bool(replication),
            bool(bypass_rls),
        )
        for (
            role_name,
            inherits,
            superuser,
            create_database,
            create_role,
            replication,
            bypass_rls,
        ) in connection.execute(
            text(
                "SELECT rolname, rolinherit, rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, rolbypassrls FROM pg_roles "
                "WHERE rolname IN (:sync_role, :read_role)"
            ),
            {"sync_role": DEMO_SYNC_ROLE, "read_role": DEMO_READ_ROLE},
        )
    }
    expected_attributes = (False, False, False, False, False, False)
    if (
        role_rows.get(DEMO_SYNC_ROLE) != expected_attributes
        or role_rows.get(DEMO_READ_ROLE) != expected_attributes
    ):
        raise DemoSchemaBootstrapError("demo roles have unsafe PostgreSQL attributes")
    has_role_membership = bool(
        connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_auth_members AS membership "
                "JOIN pg_roles AS granted ON granted.oid = membership.roleid "
                "JOIN pg_roles AS member ON member.oid = membership.member "
                "WHERE granted.rolname IN (:sync_role, :read_role) "
                "OR member.rolname IN (:sync_role, :read_role))"
            ),
            {"sync_role": DEMO_SYNC_ROLE, "read_role": DEMO_READ_ROLE},
        )
    )
    if has_role_membership:
        raise DemoSchemaBootstrapError("demo roles cannot participate in role membership")
    for role_name in (DEMO_SYNC_ROLE, DEMO_READ_ROLE):
        can_create_public = bool(
            connection.scalar(
                text("SELECT has_schema_privilege(:role, :schema, 'CREATE')"),
                {"role": role_name, "schema": PRODUCTION_SCHEMA},
            )
        )
        if can_create_public:
            raise DemoSchemaBootstrapError(
                f"role {role_name!r} can create objects in the production schema"
            )
    for table_name, column_name in source_column_rows:
        qualified = f"{PRODUCTION_SCHEMA}.{table_name}"
        read_can_select = bool(
            connection.scalar(
                text("SELECT has_column_privilege(:role, :table, :column, 'SELECT')"),
                {"role": DEMO_READ_ROLE, "table": qualified, "column": column_name},
            )
        )
        if read_can_select:
            raise DemoSchemaBootstrapError("the demo read role can select production data")
        sync_can_select = bool(
            connection.scalar(
                text("SELECT has_column_privilege(:role, :table, :column, 'SELECT')"),
                {"role": DEMO_SYNC_ROLE, "table": qualified, "column": column_name},
            )
        )
        expected = column_name in source_columns.get(table_name, ())
        if sync_can_select != expected:
            raise DemoSchemaBootstrapError(
                f"the demo sync role has unexpected production access for {qualified}.{column_name}"
            )
    for table_name in source_tables:
        qualified = f"{PRODUCTION_SCHEMA}.{table_name}"
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if connection.scalar(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": DEMO_SYNC_ROLE, "table": qualified, "privilege": privilege},
            ):
                raise DemoSchemaBootstrapError(
                    f"the demo sync role has {privilege} on production table {qualified}"
                )
    for table_name in demo_tables:
        qualified = f"{DEMO_SCHEMA}.{table_name}"
        if not connection.scalar(
            text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {"role": DEMO_READ_ROLE, "table": qualified},
        ):
            raise DemoSchemaBootstrapError(
                f"the demo read role cannot select Demo table {qualified}"
            )
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if connection.scalar(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": DEMO_READ_ROLE, "table": qualified, "privilege": privilege},
            ):
                raise DemoSchemaBootstrapError(
                    f"the demo read role has {privilege} on Demo table {qualified}"
                )
    if demo_table_count == 0:
        raise DemoSchemaBootstrapError("the demo schema migration did not create any tables")
    return DemoSchemaBootstrapResult(
        schema=DEMO_SCHEMA,
        sync_role=DEMO_SYNC_ROLE,
        read_role=DEMO_READ_ROLE,
        source_table_count=len(source_columns),
        readable_source_column_count=sum(len(columns) for columns in source_columns.values()),
        demo_table_count=demo_table_count,
    )


def _execute_composed(connection: Connection, statement: sql.Composed) -> None:
    raw_connection: Any = connection.connection.driver_connection
    if not isinstance(raw_connection, PsycopgConnection):
        raise DemoSchemaBootstrapError("demo schema bootstrap requires psycopg 3")
    typed_connection = cast(PsycopgConnection[tuple[Any, ...]], raw_connection)
    try:
        with typed_connection.cursor() as cursor:
            cursor.execute(statement)
    except PsycopgError:
        raise DemoSchemaBootstrapError("PostgreSQL rejected the demo schema bootstrap") from None
