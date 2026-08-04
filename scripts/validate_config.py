"""Validate required configuration without printing secret values."""
from __future__ import annotations
import argparse
import os

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("database", "production"), required=True)
    args = parser.parse_args()
    required = (("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
                if args.mode == "database" else
                ("ROUTING_SNAPSHOT_PATH", "API_CORS_ORIGINS"))
    absent = [name for name in required if not os.getenv(name, "").strip()]
    if absent:
        raise SystemExit("Missing required environment variables: " + ", ".join(absent))
    if args.mode == "database":
        try:
            int(os.environ["DB_PORT"])
        except ValueError as exc:
            raise SystemExit("DB_PORT must be an integer") from exc
    print(f"{args.mode} configuration is valid")

if __name__ == "__main__":
    main()
