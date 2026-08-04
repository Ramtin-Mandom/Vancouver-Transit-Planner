"""Repeatable fixture-safe snapshot load/search benchmark."""
import argparse
import json
import time
import statistics
from datetime import date, timedelta
from pathlib import Path
from src.routing.snapshot import (RoutingSnapshot, SnapshotPlanner,
                                  _geographic_heuristic_metadata)

parser=argparse.ArgumentParser(); parser.add_argument("path", type=Path); parser.add_argument("--origin"); parser.add_argument("--destination"); parser.add_argument("--iterations", type=int, default=7); parser.add_argument("--departure-seconds", type=int, default=28_800); parser.add_argument("--timeout-seconds", type=float, default=30.0); parser.add_argument("--revalidate-legacy-metadata", action="store_true")
args=parser.parse_args(); started=time.perf_counter(); snapshot=RoutingSnapshot(args.path); load_ms=(time.perf_counter()-started)*1000
result={"snapshot_size_bytes":sum(p.stat().st_size for p in args.path.iterdir()),"load_ms":load_ms,"runtime_timetable_sql_queries":0}
query=str(snapshot.arrays["stop_names"][0])[:3]; started=time.perf_counter(); snapshot.search_stops(query); result["first_stop_search_ms"]=(time.perf_counter()-started)*1000
if args.origin and args.destination:
 if args.revalidate_legacy_metadata:
  snapshot.heuristic_metadata=_geographic_heuristic_metadata(snapshot.arrays)
 planner=SnapshotPlanner(snapshot); runs={}
 for alternatives in (False, True):
  for algorithm in ("dijkstra", "astar"):
   samples=[]; counters=None
   for _ in range(args.iterations):
    started=time.perf_counter(); routed=planner.get_ranked_route_result(args.origin,args.destination,date.today(),timedelta(seconds=args.departure_seconds),algorithm=algorithm,include_alternatives=alternatives,include_diagnostics=True,timeout_seconds=args.timeout_seconds); samples.append((time.perf_counter()-started)*1000); counters=routed.diagnostics.counters
   runs[f"{algorithm}_{'alternatives' if alternatives else 'single'}"]={"median_total_ms":statistics.median(samples),"median_search_ms":statistics.median(samples),"labels_pushed":counters.states_pushed,"labels_popped":counters.states_popped,"connections_examined":counters.connections_examined,"transfer_records_examined":counters.transfer_edges_examined,"heuristic_enabled":counters.geographic_heuristic_enabled,"heuristic_values_computed":counters.heuristic_evaluations,"heuristic_cache_hits":counters.heuristic_cache_hits}
 result["runs"]=runs
print(json.dumps(result,indent=2))
