import logging
from typing import Set

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .models import Base


def _add_missing_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    metadata = Base.metadata

    existing_tables: Set[str] = set(inspector.get_table_names())

    for table in metadata.tables.values():
        table_name = table.name
        if table_name not in existing_tables:
            # Tables are created elsewhere via create_all; skip here.
            continue

        existing_columns = {col_info["name"] for col_info in inspector.get_columns(table_name)}

        for desired_column in table.columns:
            if desired_column.name in existing_columns:
                continue

            # Build ADD COLUMN DDL
            preparer = connection.dialect.identifier_preparer
            table_quoted = preparer.format_table(table)
            column_name_quoted = preparer.quote(desired_column.name)
            column_type_sql = desired_column.type.compile(dialect=connection.dialect)

            default_clause = ""
            server_default = getattr(desired_column, "server_default", None)
            if server_default is not None and getattr(server_default, "arg", None) is not None:
                try:
                    compiled_default = str(
                        server_default.arg.compile(dialect=connection.dialect)
                    )
                    default_clause = f" DEFAULT {compiled_default}"
                except Exception:  # best-effort
                    pass

            # For safety, add new columns as NULLable to avoid failures on existing rows
            # If strict NOT NULL is needed, it can be enforced manually later.
            ddl = f"ALTER TABLE {table_quoted} ADD COLUMN {column_name_quoted} {column_type_sql}{default_clause}"

            logging.info(
                f"Migrator: adding missing column {desired_column.name} to table {table_name}"
            )
            connection.execute(text(ddl))


def _relax_legacy_panel_uuid_columns(connection: Connection) -> None:
    """
    Remnawave 3.x replaced the user UUID with a numeric id, so panel_user_uuid is no
    longer written. The column stays in place to preserve the pre-3.x mapping for audits,
    but its NOT NULL constraint would break every new insert.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())

    for table_name in ("users", "subscriptions"):
        if table_name not in existing_tables:
            continue
        for column in inspector.get_columns(table_name):
            if column["name"] == "panel_user_uuid" and not column["nullable"]:
                logging.info(
                    f"Migrator: dropping NOT NULL on legacy {table_name}.panel_user_uuid"
                )
                connection.execute(
                    text(f"ALTER TABLE {table_name} ALTER COLUMN panel_user_uuid DROP NOT NULL")
                )


def _backfill_subscription_panel_user_id(connection: Connection) -> None:
    """
    subscriptions.panel_user_id is derivable from the owning user once that user has been
    linked. Users themselves are backfilled through the panel API, keyed on the tg_<id>
    username, because the pre-3.x UUID no longer exists on the panel side.
    """
    inspector = inspect(connection)
    if "subscriptions" not in set(inspector.get_table_names()):
        return

    columns = {c["name"] for c in inspector.get_columns("subscriptions")}
    if "panel_user_id" not in columns:
        return

    result = connection.execute(
        text(
            "UPDATE subscriptions s SET panel_user_id = u.panel_user_id "
            "FROM users u WHERE u.user_id = s.user_id "
            "AND s.panel_user_id IS NULL AND u.panel_user_id IS NOT NULL"
        )
    )
    if result.rowcount:
        logging.info(
            f"Migrator: backfilled panel_user_id for {result.rowcount} subscriptions"
        )

def _backfill_user_payment_methods_from_billing(connection: Connection) -> None:
    """
    Cards saved during a purchase used to be written only to the legacy user_billing row,
    while auto-renew charges and the Payment Methods screen read user_payment_methods.
    Copy them over for users who have no method on file yet, so they can be charged and
    removed. Users who already have one keep it: every payment yields its own token for
    the same card, and a second row would only duplicate it.
    """
    tables = set(inspect(connection).get_table_names())
    if not {"user_billing", "user_payment_methods"} <= tables:
        return
    result = connection.execute(
        text(
            "INSERT INTO user_payment_methods "
            "(user_id, provider, provider_payment_method_id, card_last4, card_network, is_default) "
            "SELECT b.user_id, 'yookassa', b.yookassa_payment_method_id, b.card_last4, b.card_network, TRUE "
            "FROM user_billing b "
            "WHERE b.yookassa_payment_method_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM user_payment_methods m WHERE m.user_id = b.user_id) "
            "AND NOT EXISTS (SELECT 1 FROM user_payment_methods m "
            "WHERE m.provider_payment_method_id = b.yookassa_payment_method_id)"
        )
    )
    if result.rowcount:
        logging.info(
            f"Migrator: copied {result.rowcount} saved card(s) from user_billing to user_payment_methods"
        )


def run_simple_migrations(connection: Connection) -> None:
    """
    Run lightweight, idempotent migrations:
    - Ensure missing columns are added to existing tables to match models in db/models.py
    Note: Table creation is handled separately via Base.metadata.create_all.
    """
    try:
        _add_missing_columns(connection)
        _relax_legacy_panel_uuid_columns(connection)
        _backfill_subscription_panel_user_id(connection)
        _backfill_user_payment_methods_from_billing(connection)
        logging.info("Migrator: schema synchronized (columns added as needed).")
    except Exception as e:
        logging.error(f"Migrator: failed to run simple migrations: {e}", exc_info=True)
        raise
