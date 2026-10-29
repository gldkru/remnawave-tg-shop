import logging
from types import SimpleNamespace

import db.database_setup as database_setup

PASSWORD = "s3cr3t-P@ss"


def test_engine_startup_log_masks_the_password(monkeypatch, caplog):
    monkeypatch.setattr(database_setup, "async_engine", None)
    settings = SimpleNamespace(
        DATABASE_URL=f"postgresql+asyncpg://shop:{PASSWORD.replace('@', '%40')}@db.internal:5432/vpn_shop_db")

    with caplog.at_level(logging.INFO):
        database_setup.init_db_connection(settings)

    startup_lines = [r.getMessage() for r in caplog.records if "SQLAlchemy engine" in r.getMessage()]
    assert startup_lines == [
        "Attempting to create SQLAlchemy engine with URL: "
        "postgresql+asyncpg://shop:***@db.internal:5432/vpn_shop_db"
    ]
    assert all(PASSWORD not in r.getMessage() and "s3cr3t" not in r.getMessage()
               for r in caplog.records)
