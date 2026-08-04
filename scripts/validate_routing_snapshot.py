"""Validate and summarize a routing snapshot."""
import argparse
import json
from src.routing.snapshot import RoutingSnapshot

parser = argparse.ArgumentParser()
parser.add_argument("path")
args = parser.parse_args()
snapshot = RoutingSnapshot(args.path)
try:
    print(json.dumps(snapshot.manifest, indent=2, sort_keys=True))
finally:
    snapshot.close()
