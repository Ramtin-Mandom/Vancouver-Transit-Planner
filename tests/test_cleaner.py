import csv

from src.data_ingestion.cleaner import clean_file
from src.data_ingestion.loader import TableSpec
from src.data_ingestion.parsers import Column


def test_clean_file_removes_orphan_but_preserves_optional_blank(tmp_path):
    path = tmp_path / "directions.txt"
    path.write_text(
        "direction,direction_id,route_id,route_short_name\n"
        "NORTH,0,present,,present\n"
        "SOUTH,1,missing,226,missing\n",
        encoding="utf-8",
    )
    spec = TableSpec(
        "directions",
        "directions.txt",
        (
            Column("direction", nullable=False),
            Column("direction_id", "integer", nullable=False),
            Column("route_id", nullable=False),
            Column("route_short_name"),
            Column("route_do"),
        ),
        ("route_do",),
    )

    result, _ = clean_file(path, spec, {"routes": {"present"}}, dry_run=False)

    assert result.kept == 1
    assert result.removed == 1
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["NORTH", "0", "present", "", "present"]


def test_clean_file_dry_run_does_not_modify_source(tmp_path):
    path = tmp_path / "directions.txt"
    original = (
        "direction,direction_id,route_id,route_short_name\n"
        "NORTH,0,missing,226,missing\n"
    )
    path.write_text(original, encoding="utf-8")
    spec = TableSpec(
        "directions",
        "directions.txt",
        (
            Column("direction", nullable=False),
            Column("direction_id", "integer", nullable=False),
            Column("route_id", nullable=False),
            Column("route_short_name"),
            Column("route_do"),
        ),
        ("route_do",),
    )

    result, _ = clean_file(path, spec, {"routes": set()}, dry_run=True)

    assert result.removed == 1
    assert path.read_text(encoding="utf-8") == original


def test_clean_file_removes_orphaned_route_name_extension(tmp_path):
    path = tmp_path / "route_names_exceptions.txt"
    path.write_text(
        "route_id,route_name,route_do,name_type\n"
        "active,Active Route,1,short\n"
        "53124,Retired Route,2,short\n",
        encoding="utf-8",
    )
    spec = TableSpec(
        "route_names_exceptions",
        "route_names_exceptions.txt",
        (
            Column("route_id", nullable=False),
            Column("route_name", nullable=False),
            Column("route_do"),
            Column("name_type"),
        ),
    )

    result, _ = clean_file(path, spec, {"routes": {"active"}}, dry_run=False)

    assert result.kept == 1
    assert result.removed == 1
    assert result.reasons == {"orphan route_id='53124' (missing from routes)": 1}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["active", "Active Route", "1", "short"]
