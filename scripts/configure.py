"""Create ignored local website credentials."""
import argparse
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-password", help="Explicitly replace the dashboard password")
    args = parser.parse_args()
    env_file = ROOT / ".env"
    if (ROOT / "include/device_secrets.h").exists() and not env_file.exists():
        raise SystemExit("Create .env with DEVICE_ID and DEVICE_TOKEN from the existing device_secrets.h.")
    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    defaults = {"DEVICE_ID": "streetlamp-01", "DEVICE_TOKEN": secrets.token_urlsafe(32),
                "DASHBOARD_USERNAME": "admin", "DASHBOARD_PASSWORD": secrets.token_urlsafe(16),
                "SESSION_SECRET": secrets.token_urlsafe(48),
                "MYSQL_HOST":"127.0.0.1", "MYSQL_PORT":"3306", "MYSQL_USER":"streetlamp",
                "MYSQL_PASSWORD":secrets.token_urlsafe(24), "MYSQL_DATABASE":"smart_street_lamp",
                "MYSQL_TEST_DATABASE":"smart_street_lamp_test",
                "COOKIE_SECURE": "false"}
    changed = not env_file.exists()
    for key, value in defaults.items():
        if key not in values or values[key].startswith("replace-"):
            values[key] = value
            changed = True
    if args.admin_password is not None:
        if not args.admin_password:
            raise SystemExit("Password cannot be empty")
        values["DASHBOARD_PASSWORD"] = args.admin_password
        values["SESSION_SECRET"] = secrets.token_urlsafe(48)
        changed = True
    if "DATABASE_PATH" in values:
        values.pop("DATABASE_PATH")
        changed = True
    if changed:
        env_file.write_text("# Local credentials - never commit this file.\n" +
                           "\n".join(f"{k}={v}" for k,v in values.items()) + "\n", encoding="utf-8")
    if any(not values.get(key) for key in defaults):
        raise SystemExit("Incomplete .env. Compare .env.example; existing credentials were preserved.")
    print("Local configuration ready. Dashboard credentials are in .env.")


if __name__ == "__main__":
    main()
