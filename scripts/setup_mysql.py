"""Create project-only schemas/account on the existing local MySQL server."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pymysql
from server.app import Settings, read_env


def setup(include_test=False, admin_user="root"):
    settings = Settings.load()
    config = settings.database
    values = read_env(ROOT / ".env")
    schemas = [config.database]
    if include_test:
        test_name = values.get("MYSQL_TEST_DATABASE", "smart_street_lamp_test")
        if not test_name.endswith("_test") or test_name == config.database:
            raise ValueError("Test schema must be separate and end with _test")
        from server.database import MySQLConfig
        MySQLConfig(database=test_name)  # validate identifier before DDL interpolation
        schemas.append(test_name)
    connection = pymysql.connect(host=config.host, port=config.port, user=admin_user,
        password=os.environ.get("MYSQL_ADMIN_PASSWORD", ""), charset="utf8mb4", autocommit=True)
    try:
        with connection.cursor() as cursor:
            for schema in schemas:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                # Add one nullable field to existing installations. Runtime users
                # retain their scoped privileges and do not need ALTER access.
                cursor.execute(f"SHOW TABLES FROM `{schema}` LIKE 'commands'")
                if cursor.fetchone():
                    cursor.execute(f"SHOW COLUMNS FROM `{schema}`.`commands` LIKE 'environment'")
                    if not cursor.fetchone():
                        cursor.execute(f"ALTER TABLE `{schema}`.`commands` ADD COLUMN environment VARCHAR(12) NULL AFTER auto_profile")
            for host in ("localhost", "127.0.0.1"):
                cursor.execute("SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s", (config.user, host))
                if cursor.fetchone()[0] == 0:
                    cursor.execute("CREATE USER %s@%s IDENTIFIED BY %s", (config.user, host, config.password))
                # Keep an existing account password unchanged.
                for schema in schemas:
                    cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX ON `{schema}`.* TO %s@%s", (config.user, host))
        check = config.connect()
        with check.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            version = cursor.fetchone()["version"]
        check.close()
        print("MySQL ready:", config.host, config.port, ", ".join(schemas), "server", version)
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-test", action="store_true")
    parser.add_argument("--admin-user", default="root")
    args = parser.parse_args()
    setup(args.include_test, args.admin_user)
