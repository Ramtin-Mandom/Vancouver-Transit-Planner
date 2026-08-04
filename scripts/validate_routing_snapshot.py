"""Validate and summarize a routing snapshot."""
import argparse
from src.routing.snapshot import RoutingSnapshot

parser = argparse.ArgumentParser()
parser.add_argument("path")
args = parser.parse_args()
snapshot = RoutingSnapshot(args.path)
print(snapshot.manifest)
