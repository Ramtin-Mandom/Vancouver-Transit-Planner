"""Compact, read-only routing snapshot and array-native journey planner."""

from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Mapping

import numpy as np

from src.reliability.classification import time_window
from src.reliability.models import ProfileSelection, ReliabilityProfile

from .models import (Itinerary, LegStop, ReliableAlternative, ReliableSearchResult,
                     RouteLeg, SearchCacheStatistics, SearchDiagnosticCounters,
                     SearchDiagnostics, SearchDiagnosticTimings, SearchTiming, Stop)
from .route_results import ranked_search_result
from .snapshot_search import (connection_path, search as snapshot_search,
                              validate_label_path)

FORMAT_VERSION = 2
MAX_ALTERNATIVES = 3
REQUIRED_ARRAYS = {
    "stop_ids", "stop_names", "stop_codes", "stop_lat", "stop_lon",
    "trip_ids", "route_ids", "route_names", "service_ids", "from_stop",
    "to_stop", "departure_seconds", "arrival_seconds", "trip_index",
    "route_index", "service_index", "stop_sequence", "direction_id",
    "scan_order", "departure_order", "departure_offsets",
    "service_start_ordinal", "service_end_ordinal", "service_weekday_mask",
    "exception_service", "exception_date_ordinal", "exception_type",
    "profile_route", "profile_direction", "profile_window", "profile_probability", "profile_samples",
    "parent_station", "pickup_type", "drop_off_type", "transfer_from",
    "transfer_to", "transfer_type", "transfer_seconds",
}


class SnapshotError(RuntimeError):
    """A snapshot is missing, corrupt, or incompatible."""


def _rss_bytes() -> int | None:
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if os.name == "nt" else value * 1024)
    except Exception:
        try:  # Windows peak working set, without adding a runtime dependency.
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype = wintypes.HANDLE
            get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD
            )
            get_memory.restype = wintypes.BOOL
            handle = get_process()
            if get_memory(
                handle, ctypes.byref(counters), counters.cb
            ):
                return int(counters.PeakWorkingSetSize)
        except Exception:  # pragma: no cover - platform dependent
            pass
        return None


def _check_peak_rss(limit_bytes: int | None) -> int | None:
    peak = _rss_bytes()
    if limit_bytes is not None and peak is not None and peak > limit_bytes:
        raise SnapshotError(
            f"snapshot build peak RSS {peak} bytes exceeded limit {limit_bytes} bytes"
        )
    return peak


def _open_array(directory: Path, name: str, dtype: np.dtype | str, length: int):
    return np.lib.format.open_memmap(
        directory / f"{name}.npy", mode="w+", dtype=dtype, shape=(length,)
    )


def _close_mmaps(arrays: Mapping[str, np.ndarray]) -> None:
    for array in arrays.values():
        mmap = getattr(array, "_mmap", None)
        if mmap is not None:
            mmap.close()


def _small_unsigned(maximum: int) -> np.dtype:
    return np.dtype("uint16" if maximum <= np.iinfo(np.uint16).max else "uint32")


def _strings(values: Iterable[Any]) -> np.ndarray:
    materialized = ["" if value is None else str(value) for value in values]
    width = max(1, *(len(value) for value in materialized))
    return np.asarray(materialized, dtype=f"<U{width}")


def build_snapshot_from_rows(
    output: str | Path, *, stops: Iterable[Mapping[str, Any]],
    connections: Iterable[Mapping[str, Any]], source_version: str = "fixture",
    calendars: Iterable[Mapping[str, Any]] = (),
    calendar_dates: Iterable[Mapping[str, Any]] = (),
    reliability_profiles: Iterable[Mapping[str, Any]] = (),
    transfers: Iterable[Mapping[str, Any]] = (),
    intra_station_transfer_seconds: int = 120,
    max_peak_rss_bytes: int | None = None,
) -> dict[str, Any]:
    """Build atomically using a bounded-memory, disk-backed two-pass flow."""
    started = perf_counter()
    output = Path(output)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    temporary.mkdir(parents=True, exist_ok=False)
    stop_rows = list(stops)  # the unique stop table is intentionally small
    stop_ids = [str(row["stop_id"]) for row in stop_rows]
    stop_map = {value: index for index, value in enumerate(stop_ids)}
    if len(stop_map) != len(stop_ids):
        raise SnapshotError("duplicate stop_id in snapshot input")

    dictionaries: dict[str, dict[str, int]] = {
        name: {} for name in ("trip", "route", "service")
    }
    route_names: list[str] = []
    spool = temporary / "connections.jsonl"
    count = 0
    arrays: dict[str, np.ndarray] = {}
    try:
        with spool.open("w", encoding="utf-8", newline="\n") as spool_file:
            for row in connections:
                for key in ("from_stop_id", "to_stop_id", "trip_id", "route_id",
                            "service_id", "departure_seconds", "arrival_seconds"):
                    if row.get(key) is None:
                        raise SnapshotError(f"connection missing required field {key}")
                departure = int(row["departure_seconds"])
                arrival = int(row["arrival_seconds"])
                if departure < 0 or arrival < departure:
                    raise SnapshotError("invalid connection time ordering")
                try:
                    from_stop = stop_map[str(row["from_stop_id"])]
                    to_stop = stop_map[str(row["to_stop_id"])]
                except KeyError as exc:
                    raise SnapshotError(
                        f"connection references unknown stop {exc.args[0]}"
                    ) from exc
                indexes = []
                for kind in ("trip", "route", "service"):
                    value = str(row[f"{kind}_id"])
                    index = dictionaries[kind].setdefault(
                        value, len(dictionaries[kind])
                    )
                    indexes.append(index)
                    if kind == "route" and index == len(route_names):
                        route_names.append(str(row.get("route_name") or value))
                normalized = (
                    from_stop, to_stop, departure, arrival, *indexes,
                    int(row.get("stop_sequence", 0)),
                    -1 if row.get("direction_id") is None else int(row["direction_id"]),
                    int(row.get("pickup_type") or 0),
                    int(row.get("drop_off_type") or 0),
                )
                spool_file.write(json.dumps(normalized, separators=(",", ":")) + "\n")
                count += 1
                if count % 100_000 == 0:
                    _check_peak_rss(max_peak_rss_bytes)

        stop_dtype = _small_unsigned(max(0, len(stop_ids) - 1))
        arrays = {
            "stop_ids": _strings(stop_ids),
            "stop_names": _strings(row["stop_name"] for row in stop_rows),
            "stop_codes": _strings(row.get("stop_code") for row in stop_rows),
            "stop_lat": np.asarray([row.get("stop_lat", np.nan) for row in stop_rows], dtype="float32"),
            "stop_lon": np.asarray([row.get("stop_lon", np.nan) for row in stop_rows], dtype="float32"),
            "parent_station": np.asarray([
                stop_map.get(str(row.get("parent_station")), -1)
                if row.get("parent_station") else -1 for row in stop_rows
            ], dtype="int32"),
            "trip_ids": _strings(dictionaries["trip"].keys()),
            "route_ids": _strings(dictionaries["route"].keys()),
            "route_names": _strings(route_names),
            "service_ids": _strings(dictionaries["service"].keys()),
        }
        connection_specs = {
            "from_stop": stop_dtype,
            "to_stop": stop_dtype,
            "departure_seconds": np.dtype("uint32"),
            "arrival_seconds": np.dtype("uint32"),
            "trip_index": _small_unsigned(max(0, len(dictionaries["trip"]) - 1)),
            "route_index": _small_unsigned(max(0, len(dictionaries["route"]) - 1)),
            "service_index": _small_unsigned(max(0, len(dictionaries["service"]) - 1)),
            "stop_sequence": np.dtype("uint16"),
            "direction_id": np.dtype("int8"),
            "pickup_type": np.dtype("uint8"),
            "drop_off_type": np.dtype("uint8"),
        }
        for name, dtype in connection_specs.items():
            arrays[name] = _open_array(temporary, name, dtype, count)
        with spool.open("r", encoding="utf-8") as spool_file:
            for position, line in enumerate(spool_file):
                values = json.loads(line)
                for column, value in zip(connection_specs, values):
                    arrays[column][position] = value
        spool.unlink()
        for name in connection_specs:
            arrays[name].flush()
        _check_peak_rss(max_peak_rss_bytes)
        calendar_by_service = {str(row["service_id"]): row for row in calendars}
        starts=[]; ends=[]; masks=[]
        for service_id in dictionaries["service"]:
            rule=calendar_by_service.get(service_id)
            if rule:
                starts.append(rule["start_date"].toordinal()); ends.append(rule["end_date"].toordinal())
                masks.append(sum((1 << day) for day,name in enumerate(("monday","tuesday","wednesday","thursday","friday","saturday","sunday")) if rule[name]))
            else:
                starts.append(0); ends.append(np.iinfo(np.int32).max); masks.append(127)
        arrays["service_start_ordinal"]=np.asarray(starts,dtype="int32")
        arrays["service_end_ordinal"]=np.asarray(ends,dtype="int32")
        exception_rows=[row for row in calendar_dates if str(row["service_id"]) in dictionaries["service"]]
        exception_rows.sort(key=lambda row:(row["service_date"],str(row["service_id"])))
        arrays["service_weekday_mask"]=np.asarray(masks,dtype="uint8")
        arrays["exception_service"]=np.asarray([dictionaries["service"][str(row["service_id"])] for row in exception_rows],dtype=_small_unsigned(max(0,len(dictionaries["service"])-1)))
        arrays["exception_date_ordinal"]=np.asarray([row["service_date"].toordinal() for row in exception_rows],dtype="int32")
        arrays["exception_type"]=np.asarray([row["exception_type"] for row in exception_rows],dtype="uint8")
        window_names=[name for name,_,_ in (("overnight",0,6),("morning_peak",6,10),("midday",10,15),("afternoon_peak",15,19),("evening",19,24))]
        profiles=[]
        for row in reliability_profiles:
            route=dictionaries["route"].get(str(row.get("route_id")))
            if route is None or row.get("time_window") not in window_names:
                continue
            profiles.append((route, -1 if row.get("direction_id") is None else int(row["direction_id"]), window_names.index(row["time_window"]), float(row["reliability_probability"]), int(row["sample_count"])))
        arrays["profile_route"]=np.asarray([p[0] for p in profiles],dtype="uint32")
        arrays["profile_direction"]=np.asarray([p[1] for p in profiles],dtype="int8")
        arrays["profile_window"]=np.asarray([p[2] for p in profiles],dtype="uint8")
        arrays["profile_probability"]=np.asarray([p[3] for p in profiles],dtype="float32")
        arrays["profile_samples"]=np.asarray([p[4] for p in profiles],dtype="uint32")
        transfer_rows = list(transfers)
        explicit_pairs = {
            (str(row["from_stop_id"]), str(row["to_stop_id"]))
            for row in transfer_rows
        }
        station_members: dict[str, list[str]] = {}
        for row in stop_rows:
            if row.get("parent_station"):
                station_members.setdefault(str(row["parent_station"]), []).append(str(row["stop_id"]))
        for members in station_members.values():
            for from_id in members:
                for to_id in members:
                    if from_id != to_id and (from_id, to_id) not in explicit_pairs:
                        transfer_rows.append({"from_stop_id": from_id, "to_stop_id": to_id,
                                              "transfer_type": 2,
                                              "min_transfer_time": intra_station_transfer_seconds})
        try:
            arrays["transfer_from"] = np.asarray(
                [stop_map[str(row["from_stop_id"])] for row in transfer_rows], dtype=stop_dtype)
            arrays["transfer_to"] = np.asarray(
                [stop_map[str(row["to_stop_id"])] for row in transfer_rows], dtype=stop_dtype)
        except KeyError as exc:
            raise SnapshotError(f"transfer references unknown stop {exc.args[0]}") from exc
        arrays["transfer_type"] = np.asarray(
            [int(row.get("transfer_type") or 0) for row in transfer_rows], dtype="uint8")
        arrays["transfer_seconds"] = np.asarray(
            [int(row.get("min_transfer_time") or 0) for row in transfer_rows], dtype="uint32")
        index_dtype = np.dtype("uint32" if count <= np.iinfo(np.uint32).max else "uint64")
        arrays["scan_order"] = np.argsort(
            arrays["departure_seconds"], kind="stable"
        ).astype(index_dtype, copy=False)
        _check_peak_rss(max_peak_rss_bytes)
        arrays["departure_order"] = np.lexsort(
            (arrays["departure_seconds"], arrays["from_stop"])
        ).astype(index_dtype, copy=False)
        counts_by_stop = np.bincount(arrays["from_stop"].astype(np.int64), minlength=len(stop_ids))
        arrays["departure_offsets"] = np.concatenate(([0], np.cumsum(counts_by_stop))).astype(index_dtype)
        for name, array in arrays.items():
            path = temporary / f"{name}.npy"
            if not path.exists():
                np.save(path, array, allow_pickle=False)
        peak_rss = _check_peak_rss(max_peak_rss_bytes)
        array_size = sum(path.stat().st_size for path in temporary.glob("*.npy"))
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_version": source_version,
            "compatibility": {"minimum_loader_version": 2},
            "counts": {"stops": len(stop_ids), "routes": len(dictionaries["route"]),
                       "trips": len(dictionaries["trip"]), "connections": count},
            "arrays": {name: {"shape": list(array.shape), "dtype": str(array.dtype)} for name, array in arrays.items()},
            "build": {
                "duration_seconds": perf_counter() - started,
                "size_bytes": array_size,
                "peak_rss_bytes": peak_rss,
            },
        }
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Windows cannot rename a directory containing open mmap handles.
        _close_mmaps(arrays)
        backup = output.with_name(f".{output.name}.old-{os.getpid()}")
        if output.exists():
            os.replace(output, backup)
        try:
            os.replace(temporary, output)
        except Exception:
            if backup.exists():
                os.replace(backup, output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        size = sum(path.stat().st_size for path in output.iterdir())
        manifest["build"]["size_bytes"] = size
        return manifest
    except Exception:
        _close_mmaps(arrays)
        shutil.rmtree(temporary, ignore_errors=True)
        raise


class RoutingSnapshot:
    """One process-wide mmap-backed immutable timetable."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.manifest = json.loads((self.path / "manifest.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise SnapshotError("snapshot manifest is missing or invalid") from exc
        if self.manifest.get("format_version") != FORMAT_VERSION:
            raise SnapshotError(f"unsupported snapshot format {self.manifest.get('format_version')!r}")
        declared = set(self.manifest.get("arrays", {}))
        missing = REQUIRED_ARRAYS - declared
        if missing:
            raise SnapshotError(f"snapshot is missing arrays: {', '.join(sorted(missing))}")
        self.arrays: dict[str, np.ndarray] = {}
        for name, spec in self.manifest["arrays"].items():
            try:
                array = np.load(self.path / f"{name}.npy", mmap_mode="r", allow_pickle=False)
            except Exception as exc:
                raise SnapshotError(f"cannot load snapshot array {name}") from exc
            if list(array.shape) != spec["shape"] or str(array.dtype) != spec["dtype"]:
                raise SnapshotError(f"snapshot array {name} does not match manifest")
            array.flags.writeable = False
            self.arrays[name] = array
        n = int(self.manifest["counts"]["connections"])
        for name in ("from_stop", "to_stop", "departure_seconds", "arrival_seconds",
                     "trip_index", "route_index", "service_index", "stop_sequence", "direction_id"):
            if len(self.arrays[name]) != n:
                raise SnapshotError(f"snapshot array {name} has invalid length")
        if len(self.arrays["departure_offsets"]) != len(self.arrays["stop_ids"]) + 1:
            raise SnapshotError("invalid departure offsets")
        limits = {"from_stop": len(self.arrays["stop_ids"]), "to_stop": len(self.arrays["stop_ids"]),
                  "trip_index": len(self.arrays["trip_ids"]), "route_index": len(self.arrays["route_ids"]),
                  "service_index": len(self.arrays["service_ids"])}
        for name, limit in limits.items():
            if len(self.arrays[name]) and int(np.max(self.arrays[name])) >= limit:
                raise SnapshotError(f"snapshot array {name} contains an out-of-range index")
        if np.any(self.arrays["arrival_seconds"] < self.arrays["departure_seconds"]):
            raise SnapshotError("snapshot contains invalid connection time ordering")
        self._stop_id_order = np.argsort(self.arrays["stop_ids"], kind="stable")

    def close(self) -> None:
        for array in self.arrays.values():
            mmap = getattr(array, "_mmap", None)
            if mmap is not None:
                mmap.close()
        self.arrays.clear()

    def find_stop(self, stop_id: str) -> Stop | None:
        ordered = self.arrays["stop_ids"][self._stop_id_order]
        position = int(np.searchsorted(ordered, stop_id))
        index = int(self._stop_id_order[position]) if position < len(ordered) and str(ordered[position]) == stop_id else None
        return self.stop(index) if index is not None else None

    def stop_index(self, stop_id: str) -> int:
        stop = self.find_stop(stop_id)
        if stop is None:
            raise KeyError(stop_id)
        ordered = self.arrays["stop_ids"][self._stop_id_order]
        return int(self._stop_id_order[int(np.searchsorted(ordered, stop_id))])

    def stop(self, index: int) -> Stop:
        a = self.arrays
        lat, lon = float(a["stop_lat"][index]), float(a["stop_lon"][index])
        return Stop(str(a["stop_ids"][index]), str(a["stop_names"][index]),
                    str(a["stop_codes"][index]) or None,
                    None if math.isnan(lat) else lat, None if math.isnan(lon) else lon)

    def search_stops(self, query: str, limit: int = 20) -> list[Stop]:
        needle = query.casefold()
        matches = [i for i, name in enumerate(self.arrays["stop_names"])
                   if needle in str(name).casefold()]
        matches.sort(key=lambda i: (str(self.arrays["stop_names"][i]), str(self.arrays["stop_ids"][i])))
        return [self.stop(i) for i in matches[:limit]]


class SnapshotStopCatalog:
    """Read-only stop lookup usable while a routing artifact is incompatible.

    Stop metadata has been stable since snapshot v1. Loading only these arrays
    keeps autocomplete available without treating an old timetable as routable.
    """

    REQUIRED = ("stop_ids", "stop_names", "stop_codes", "stop_lat", "stop_lon")

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.manifest = json.loads(
                (self.path / "manifest.json").read_text(encoding="utf-8")
            )
            declared = self.manifest["arrays"]
            self.arrays = {
                name: np.load(self.path / f"{name}.npy", mmap_mode="r", allow_pickle=False)
                for name in self.REQUIRED
            }
        except Exception as exc:
            raise SnapshotError("snapshot stop catalog is missing or invalid") from exc
        for name, array in self.arrays.items():
            spec = declared.get(name)
            if spec is None or list(array.shape) != spec.get("shape") or str(array.dtype) != spec.get("dtype"):
                self.close()
                raise SnapshotError(f"snapshot stop array {name} does not match manifest")
            array.flags.writeable = False
        lengths = {len(array) for array in self.arrays.values()}
        if len(lengths) != 1:
            self.close()
            raise SnapshotError("snapshot stop arrays have inconsistent lengths")
        self._stop_id_order = np.argsort(self.arrays["stop_ids"], kind="stable")

    def close(self) -> None:
        for array in getattr(self, "arrays", {}).values():
            mmap = getattr(array, "_mmap", None)
            if mmap is not None:
                mmap.close()
        getattr(self, "arrays", {}).clear()

    def stop(self, index: int) -> Stop:
        a = self.arrays
        lat, lon = float(a["stop_lat"][index]), float(a["stop_lon"][index])
        return Stop(str(a["stop_ids"][index]), str(a["stop_names"][index]),
                    str(a["stop_codes"][index]) or None,
                    None if math.isnan(lat) else lat,
                    None if math.isnan(lon) else lon)

    def find_stop(self, stop_id: str) -> Stop | None:
        ordered = self.arrays["stop_ids"][self._stop_id_order]
        position = int(np.searchsorted(ordered, stop_id))
        if position >= len(ordered) or str(ordered[position]) != stop_id:
            return None
        return self.stop(int(self._stop_id_order[position]))

    def search_stops(self, query: str, limit: int = 20) -> list[Stop]:
        needle = query.casefold()
        matches = [i for i, name in enumerate(self.arrays["stop_names"])
                   if needle in str(name).casefold()]
        matches.sort(key=lambda i: (str(self.arrays["stop_names"][i]),
                                    str(self.arrays["stop_ids"][i])))
        return [self.stop(i) for i in matches[:limit]]


class SnapshotPlanner:
    """Connection-scan planner operating on mmap arrays, materializing only results."""

    def __init__(self, snapshot: RoutingSnapshot):
        self.snapshot = snapshot

    @staticmethod
    def _semantic_identity(alternative: ReliableAlternative) -> tuple:
        """Ignore internal trip-instance IDs when the useful journey is equal."""
        return tuple(
            (
                leg.route_id, leg.origin.stop_id, leg.destination.stop_id,
                leg.departure_time, leg.arrival_time,
            )
            for leg in alternative.itinerary.legs
        )

    @staticmethod
    def _pareto_survivors(
        alternatives: list[ReliableAlternative],
    ) -> list[ReliableAlternative]:
        """Keep only arrival/reliability/transfer non-dominated journeys."""
        return [
            candidate
            for candidate in alternatives
            if not any(
                other is not candidate
                and other.itinerary.arrival_time <= candidate.itinerary.arrival_time
                and other.reliability_cost <= candidate.reliability_cost
                and other.itinerary.transfer_count
                <= candidate.itinerary.transfer_count
                and (
                    other.itinerary.arrival_time < candidate.itinerary.arrival_time
                    or other.reliability_cost < candidate.reliability_cost
                    or other.itinerary.transfer_count
                    < candidate.itinerary.transfer_count
                )
                for other in alternatives
            )
        ]

    def get_ranked_route_result(self, origin_stop_id: str, destination_stop_id: str,
                                service_date: date, departure_time: timedelta, resolver=None,
                                *, route_number=MAX_ALTERNATIVES, preferences=None,
                                algorithm="astar", include_alternatives=False,
                                include_diagnostics=False, **_: Any) -> ReliableSearchResult:
        requested_algorithm = str(algorithm).strip().lower()
        if requested_algorithm == "baseline":
            requested_algorithm = "dijkstra"
        if requested_algorithm not in {"dijkstra", "astar"}:
            raise ValueError("snapshot routing algorithm must be 'dijkstra' or 'astar'")
        started = perf_counter(); a = self.snapshot.arrays
        origin = self.snapshot.stop_index(origin_stop_id); destination = self.snapshot.stop_index(destination_stop_id)
        departure_seconds = max(0, int(departure_time.total_seconds()))
        route_limit = min(
            MAX_ALTERNATIVES, max(1, int(route_number))
        ) if include_alternatives else 1
        labels, winners, stats = snapshot_search(
            a, origin, destination, departure_seconds, service_date,
            algorithm=requested_algorithm,
            max_transfers=int(_.get("max_transfers", 3)),
            search_horizon_seconds=int(_.get("search_horizon_minutes", 180)) * 60,
            max_extra_seconds=int(_.get("max_extra_minutes", 30)) * 60,
            candidate_limit=(max(8, route_limit * 4)
                             if include_alternatives else 1),
            timeout_seconds=float(_.get("timeout_seconds", 30.0)),
            collect_alternatives=include_alternatives,
        )
        materialized: list[ReliableAlternative] = []
        identities: set[tuple] = set()
        for winner in winners:
            path = connection_path(labels, winner)
            validate_label_path(a, labels, winner, origin, destination,
                                departure_seconds)
            legs=[]; selections=[]; pos=0
            while pos < len(path):
                first=path[pos]; trip=int(a["trip_index"][first]); end=pos
                while end+1 < len(path) and int(a["trip_index"][path[end+1]]) == trip: end += 1
                segment=path[pos:end+1]; f,l=segment[0],segment[-1]
                stops=[LegStop(self.snapshot.stop(int(a["from_stop"][f])), int(a["stop_sequence"][f]), None,
                               timedelta(seconds=int(a["departure_seconds"][f])))]
                for item in segment:
                    stops.append(LegStop(self.snapshot.stop(int(a["to_stop"][item])), int(a["stop_sequence"][item])+1,
                                         timedelta(seconds=int(a["arrival_seconds"][item])), None))
                route=int(a["route_index"][f]); direction=int(a["direction_id"][f]); direction_value=None if direction < 0 else direction
                legs.append(RouteLeg(str(a["trip_ids"][trip]), str(a["route_ids"][route]), str(a["route_names"][route]),
                                     stops[0].stop, stops[-1].stop, timedelta(seconds=int(a["departure_seconds"][f])),
                                     timedelta(seconds=int(a["arrival_seconds"][l])), direction_value, tuple(stops)))
                window=time_window(timedelta(seconds=int(a["departure_seconds"][f])))
                selection = resolver.resolve(str(a["route_ids"][route]), direction_value, window) if resolver else self._profile(route,direction_value,window,int(_.get("minimum_samples",20)))
                selections.append(selection); pos=end+1
            # Missing profiles use an explicit conservative fallback. They are
            # never silently treated as perfectly reliable.
            probability=1.0
            for selection in selections:
                probability *= float(selection.profile.reliability_probability) if selection.profile else 0.5
            itinerary=Itinerary(self.snapshot.stop(origin), self.snapshot.stop(destination), service_date,
                                departure_time, timedelta(seconds=labels[winner].arrival), tuple(legs))
            alternative = ReliableAlternative(
                itinerary, probability, -math.log(max(probability,1e-9)),
                tuple(selections))
            identity = self._semantic_identity(alternative)
            if identity in identities:
                continue
            identities.add(identity)
            materialized.append(alternative)
        alternatives = tuple(self._pareto_survivors(materialized))
        search_ms=(perf_counter()-started)*1000
        diagnostics = SearchDiagnostics(SearchDiagnosticTimings(measured_search_ms=search_ms),
            SearchDiagnosticCounters(
                algorithm=requested_algorithm, requested_algorithm=str(algorithm),
                executed_algorithm=requested_algorithm, states_pushed=stats.pushed,
                states_popped=stats.popped, states_reopened=stats.reopened,
                labels_created=stats.pushed,
                transfer_edges_examined=stats.transfers,
                heuristic_evaluations=stats.heuristics,
                zero_heuristic_fallbacks=stats.zero_fallbacks,
                final_arrival_cost=(labels[winners[0]].arrival if winners else 0),
                connections_examined=stats.connections,
                destination_labels_found=len(winners),
                candidate_itineraries=len(materialized),
                alternatives_reconstructed=len(alternatives)),
            SearchCacheStatistics(unexpected_queries_during_search=0)) if include_diagnostics else None
        result=ReliableSearchResult(alternatives, SearchTiming(0,search_ms,0,search_ms),0,diagnostics)
        return ranked_search_result(result, route_number=route_limit, preferences=preferences)

    def _profile(self, route: int, direction: int | None, window: str, minimum_samples: int) -> ProfileSelection:
        a=self.snapshot.arrays; names=("overnight","morning_peak","midday","afternoon_peak","evening")
        mask=(a["profile_route"] == route) & (a["profile_window"] == names.index(window)) & (a["profile_direction"] == (-1 if direction is None else direction))
        matches=np.flatnonzero(mask)
        if not len(matches):
            return ProfileSelection(None,"insufficient-data",True)
        i=int(matches[0]); probability=float(a["profile_probability"][i]); samples=int(a["profile_samples"][i])
        profile=ReliabilityProfile(str(a["route_ids"][route]),None,None,None,samples,0,0,None,0,0,0,probability,1-probability,direction,window,reliability_probability=probability)
        return ProfileSelection(profile,"route_direction_window",samples < minimum_samples)
