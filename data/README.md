# Transit data policy and attribution

Full GTFS archives, extracted feeds, and generated routing snapshots are build
inputs or artifacts and are not stored in Git. Download the current static feed:

```powershell
python -m scripts.download_gtfs
python -m src.data_ingestion.cleaner
python -m src.data_ingestion.cli --dry-run
python -m src.data_ingestion.cli --replace
python -m scripts.build_routing_snapshot --output data/routing_snapshot
python -m scripts.validate_routing_snapshot data/routing_snapshot
```

Use `--force` with `scripts.download_gtfs` to replace an existing local feed.
The downloader defaults to TransLink's published static GTFS URL. Tests create
small deterministic data in temporary directories and do not require the full
feed committed to this repository.

## Source and licence

The feed previously tracked here identified itself as TransLink version
`26JUN_20260717`, covering 2026-06-08 through 2026-09-06. This metadata is kept
for provenance only; it is not a usable timetable fixture.

"Route and arrival data used in this product or service is provided by
permission of TransLink. TransLink assumes no responsibility for the accuracy
or currency of the Data used in this product or service."

Review the current
[TransLink Open API terms](https://developer.translink.ca/TermsOfUse/WebApi)
before downloading, using, or redistributing the feed.

This independent portfolio project is not affiliated with, sponsored by, or
endorsed by TransLink. The repository's MIT licence applies to project code; it
does not automatically license TransLink data, OpenStreetMap tiles, or other
third-party material.
