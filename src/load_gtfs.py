"""Backward-compatible entry point for the PostgreSQL GTFS loader."""

from src.data_ingestion.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
