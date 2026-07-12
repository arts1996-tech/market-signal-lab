from pathlib import Path

from jobs.backup_database import pg_dump_command, pg_dump_command_without_password, pg_dump_database_url


def test_pg_dump_url_removes_sqlalchemy_driver_name():
    result = pg_dump_database_url("postgresql+psycopg://market:secret@db:5432/market_signal_lab")

    assert result == "postgresql://market:secret@db:5432/market_signal_lab"


def test_pg_dump_command_uses_custom_format_and_compatible_url():
    command = pg_dump_command(
        "postgresql+psycopg://market:secret@db:5432/market_signal_lab",
        Path("/backups/test.dump"),
    )

    assert command == [
        "pg_dump",
        "--format=custom",
        "--file",
        "/backups/test.dump",
        "postgresql://market:secret@db:5432/market_signal_lab",
    ]


def test_pg_dump_safe_command_does_not_put_password_in_url():
    command, password = pg_dump_command_without_password(
        "postgresql+psycopg://market:secret@db:5432/market_signal_lab",
        Path("/backups/test.dump"),
    )

    assert password == "secret"
    assert "secret" not in " ".join(command)
    assert command[-1] in {
        "postgresql://market@db:5432/market_signal_lab",
        "postgresql://market:@db:5432/market_signal_lab",
    }
