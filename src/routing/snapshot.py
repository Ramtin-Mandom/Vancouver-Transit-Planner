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

FORMAT_VERSION = 1
REQUIRED_ARRAYS = {
    "stop_ids", "stop_names", "stop_codes", "stop_lat", "stop_lon",
    "trip_ids", "route_ids", "route_names", "service_ids", "from_stop",
    "to_stop", "departure_seconds", "arrival_seconds", "trip_index",
    "route_index", "service_index", "stop_sequence", "direction_id",
    "scan_order", "departure_order", "departure_offsets",
    "service_start_ordinal", "service_end_ordinal", "service_weekday_mask",
    "exception_service", "exception_date_ordinal", "exception_type",
    "profile_route", "profile_direction", "profile_window", "profile_probability", "profile_samples",
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
            "compatibility": {"minimum_loader_version": 1},
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


class SnapshotPlanner:
    """Connection-scan planner operating on mmap arrays, materializing only results."""

    def __init__(self, snapshot: RoutingSnapshot):
        self.snapshot = snapshot

    def get_ranked_route_result(self, origin_stop_id: str, destination_stop_id: str,
                                service_date: date, departure_time: timedelta, resolver=None,
                                *, route_number=5, preferences=None, algorithm="astar",
                                include_diagnostics=False, **_: Any) -> ReliableSearchResult:
        started = perf_counter(); a = self.snapshot.arrays
        origin = self.snapshot.stop_index(origin_stop_id); destination = self.snapshot.stop_index(destination_stop_id)
        inf = np.iinfo(np.uint32).max
        arrivals = np.full(len(a["stop_ids"]), inf, dtype="uint32")
        predecessors = np.full(len(a["stop_ids"]), -1, dtype="int64")
        arrivals[origin] = max(0, int(departure_time.total_seconds()))
        ordinal=service_date.toordinal(); active=(ordinal >= a["service_start_ordinal"]) & (ordinal <= a["service_end_ordinal"]) & ((a["service_weekday_mask"] & (1 << service_date.weekday())) != 0)
        if len(a["exception_date_ordinal"]):
            matches=np.flatnonzero(a["exception_date_ordinal"] == ordinal)
            for match in matches:
                active[int(a["exception_service"][match])] = int(a["exception_type"][match]) == 1
        examined = 0
        for raw in a["scan_order"]:
            i = int(raw); examined += 1
            if not active[int(a["service_index"][i])]:
                continue
            source, target = int(a["from_stop"][i]), int(a["to_stop"][i])
            departure = int(a["departure_seconds"][i]); arrival = int(a["arrival_seconds"][i])
            if departure >= int(arrivals[source]) and arrival < int(arrivals[target]):
                arrivals[target] = arrival; predecessors[target] = i
        alternatives: tuple[ReliableAlternative, ...] = ()
        if predecessors[destination] >= 0:
            path=[]; cursor=destination
            while cursor != origin and predecessors[cursor] >= 0:
                i=int(predecessors[cursor]); path.append(i); cursor=int(a["from_stop"][i])
            path.reverse()
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
            probability=1.0
            for selection in selections:
                probability *= float(selection.profile.reliability_probability) if selection.profile else 1.0
            itinerary=Itinerary(self.snapshot.stop(origin), self.snapshot.stop(destination), service_date,
                                timedelta(seconds=int(a["departure_seconds"][path[0]])), timedelta(seconds=int(arrivals[destination])), tuple(legs))
            alternatives=(ReliableAlternative(itinerary, probability, -math.log(max(probability,1e-9)), tuple(selections)),)
        search_ms=(perf_counter()-started)*1000
        diagnostics = SearchDiagnostics(SearchDiagnosticTimings(measured_search_ms=search_ms),
            SearchDiagnosticCounters(algorithm=algorithm, connections_examined=examined, alternatives_reconstructed=len(alternatives)),
            SearchCacheStatistics(unexpected_queries_during_search=0)) if include_diagnostics else None
        result=ReliableSearchResult(alternatives, SearchTiming(0,search_ms,0,search_ms),0,diagnostics)
        return ranked_search_result(result, route_number=route_number, preferences=preferences)

    def _profile(self, route: int, direction: int | None, window: str, minimum_samples: int) -> ProfileSelection:
        a=self.snapshot.arrays; names=("overnight","morning_peak","midday","afternoon_peak","evening")
        mask=(a["profile_route"] == route) & (a["profile_window"] == names.index(window)) & (a["profile_direction"] == (-1 if direction is None else direction))
        matches=np.flatnonzero(mask)
        if not len(matches):
            return ProfileSelection(None,"insufficient-data",True)
        i=int(matches[0]); probability=float(a["profile_probability"][i]); samples=int(a["profile_samples"][i])
        profile=ReliabilityProfile(str(a["route_ids"][route]),None,None,None,samples,0,0,None,0,0,0,probability,1-probability,direction,window,reliability_probability=probability)
        return ProfileSelection(profile,"route_direction_window",samples < minimum_samples)
