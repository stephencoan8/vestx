"""Bare Postgres URLs must use the psycopg2 driver this image installs."""

from app.config import sqlalchemy_database_uri


def test_railway_postgres_scheme_uses_psycopg2():
    url = sqlalchemy_database_uri(
        'postgres://vestx:secret@postgres.internal:5432/vestx'
    )
    assert url == 'postgresql+psycopg2://vestx:secret@postgres.internal:5432/vestx'


def test_bare_postgresql_scheme_uses_psycopg2():
    url = sqlalchemy_database_uri(
        'postgresql://vestx:secret@localhost:5432/vestx?sslmode=require'
    )
    assert url == (
        'postgresql+psycopg2://vestx:secret@localhost:5432/vestx?sslmode=require'
    )


def test_explicit_driver_and_sqlite_are_unchanged():
    explicit = 'postgresql+psycopg2://vestx@localhost/vestx'
    assert sqlalchemy_database_uri(explicit) == explicit
    sqlite = 'sqlite:///stonks.db'
    assert sqlalchemy_database_uri(sqlite) == sqlite
