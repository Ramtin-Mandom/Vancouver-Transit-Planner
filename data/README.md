# Transit data policy and attribution

Full GTFS archives, extracted feeds, and uncompressed snapshots remain local.
The deployment archive `routing_snapshot.zip` is stored in Git. To optionally
refresh the saved dataset, download the current static feed:

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
for provenance; the deployment archive reuses those saved trips by time of day.

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


## Offline deployment artifact

`routing_snapshot.zip` is the versioned deployment dataset, containing public
GTFS routing arrays and 1,939 aggregate route/direction/time-window reliability
profiles. It contains no database credentials or raw observations. Source feed:
`26JUN_20260717`; snapshot created August 4, 2026 (format 2).

Render restores this archive instead of connecting to PostgreSQL. Calendar dates
do not expire the public time-of-day planner. Keep this file in Git when deploying.
The uncompressed `routing_snapshot/` directory remains ignored.

To update the dataset, build and validate a new snapshot with the existing offline
builder, then replace this archive with its `manifest.json` and `.npy` files at the
ZIP root. No data refresh is needed just to deploy code changes.
