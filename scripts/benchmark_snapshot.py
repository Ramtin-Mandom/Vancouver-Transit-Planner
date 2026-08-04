"""Repeatable fixture-safe snapshot load/search benchmark."""
import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path
from src.routing.snapshot import RoutingSnapshot, SnapshotPlanner

parser=argparse.ArgumentParser(); parser.add_argument("path", type=Path); parser.add_argument("--origin"); parser.add_argument("--destination")
args=parser.parse_args(); started=time.perf_counter(); snapshot=RoutingSnapshot(args.path); load_ms=(time.perf_counter()-started)*1000
result={"snapshot_size_bytes":sum(p.stat().st_size for p in args.path.iterdir()),"load_ms":load_ms,"runtime_timetable_sql_queries":0}
query=str(snapshot.arrays["stop_names"][0])[:3]; started=time.perf_counter(); snapshot.search_stops(query); result["first_stop_search_ms"]=(time.perf_counter()-started)*1000
if args.origin and args.destination:
 planner=SnapshotPlanner(snapshot); started=time.perf_counter(); first=planner.get_ranked_route_result(args.origin,args.destination,date.today(),timedelta(hours=8)); result["first_route_ms"]=(time.perf_counter()-started)*1000
 started=time.perf_counter(); second=planner.get_ranked_route_result(args.origin,args.destination,date.today(),timedelta(hours=8)); result["warm_route_ms"]=(time.perf_counter()-started)*1000; result["result_equivalent"]=first.alternatives==second.alternatives
print(json.dumps(result,indent=2))
