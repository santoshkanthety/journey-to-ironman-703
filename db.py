"""Shared Postgres connection for the ironman schema.

Set IRONMAN_DB_URL to your Postgres instance, e.g.:
    export IRONMAN_DB_URL=postgresql://user:password@localhost:5432/fitness
The `ironman` schema is created by schema_pg.sql inside that database.
"""
import os
import sys

import psycopg

DSN = os.environ.get("IRONMAN_DB_URL")


def connect() -> psycopg.Connection:
    if not DSN:
        sys.exit("IRONMAN_DB_URL not set. Example:\n"
                 "  export IRONMAN_DB_URL=postgresql://user:pass@localhost:5432/fitness")
    con = psycopg.connect(DSN)
    con.execute("SET search_path TO ironman")
    return con
