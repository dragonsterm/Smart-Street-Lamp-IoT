"""MySQL/MariaDB persistence. The application never falls back to SQLite."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re

import pymysql
from pymysql.cursors import DictCursor


@dataclass
class MySQLConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "streetlamp"
    password: str = ""
    database: str = "smart_street_lamp"
    ssl_ca: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.database):
            raise ValueError("MySQL database name must contain lowercase letters, numbers and underscores")

    def connect(self):
        return pymysql.connect(host=self.host, port=self.port, user=self.user,
            password=self.password, database=self.database, charset="utf8mb4",
            cursorclass=DictCursor, autocommit=False, connect_timeout=5,
            read_timeout=10, write_timeout=10,
            ssl_ca=self.ssl_ca, ssl_verify_cert=bool(self.ssl_ca), ssl_verify_identity=bool(self.ssl_ca))


class Session:
    def __init__(self, connection):
        self.connection = connection
        self.cursors = []

    def execute(self, sql, parameters=()):
        cursor = self.connection.cursor()
        self.cursors.append(cursor)
        cursor.execute(sql, parameters)
        return cursor

    def close(self):
        for cursor in self.cursors:
            cursor.close()


class Database:
    def __init__(self, config: MySQLConfig):
        self.config = config

    @contextmanager
    def session(self, write=False, device_id=None):
        connection = self.config.connect()
        session = Session(connection)
        try:
            connection.begin()
            if write and device_id is not None:
                # Serialize command issuance, expiry, ACK and sync for this device.
                session.execute("SELECT id FROM devices WHERE id=%s FOR UPDATE", (device_id,)).fetchone()
            yield session
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            session.close()
            connection.close()

    def initialize(self, device_id):
        with self.session() as session:
            for statement in (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    session.execute(statement)
            # Idempotent additive migration for installations created before 2.2.
            column = session.execute("SHOW COLUMNS FROM commands LIKE 'environment'").fetchone()
            if not column:
                raise RuntimeError("Database upgrade needed: run python scripts/setup_mysql.py --include-test with the local database administrator, then restart the server")
            session.execute("INSERT IGNORE INTO devices(id) VALUES (%s)", (device_id,))
