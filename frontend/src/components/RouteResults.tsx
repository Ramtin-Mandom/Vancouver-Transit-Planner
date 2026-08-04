import { BarChart3, Clock, MapPin, SearchX } from "lucide-react";
import type { RoutePlanResponse } from "../api/types";
import { RouteCard } from "./RouteCard";

function routeLabels(result: RoutePlanResponse, index: number): string[] {
  const alternative = result.alternatives[index];
  const labels: string[] = [];
  if (result.alternatives.length < 2) return labels;
  if (index === 0) labels.push("Best overall");
  const highestReliability = Math.max(
    ...result.alternatives.map((item) => item.route_reliability)
  );
  const shortestDuration = Math.min(
    ...result.alternatives.map((item) => item.duration_seconds)
  );
  if (alternative.route_reliability === highestReliability) labels.push("Most reliable");
  if (alternative.duration_seconds === shortestDuration) labels.push("Fastest");
  return labels;
}

export function RouteResults({ result }: { result: RoutePlanResponse }) {
  if (result.alternatives.length === 0) {
    return (
      <section className="emptyResults" aria-live="polite">
        <SearchX size={34} />
        <h2>No scheduled routes found</h2>
        <p>Try a different departure time, service date, or nearby stop.</p>
      </section>
    );
  }
  return (
    <section className="resultsSection" aria-labelledby="results-heading">
      <div className="resultsSummary">
        <div>
          <p className="eyebrow">Ranked results</p>
          <h2 id="results-heading">
            {result.origin.stop_name} <span>to</span> {result.destination.stop_name}
          </h2>
          <p>
            Departing from {result.requested_departure_time} on {result.service_date}.
            The engine has already ranked {result.alternatives.length}{" "}
            {result.alternatives.length === 1 ? "alternative" : "alternatives"}.
          </p>
        </div>
        <MapPin size={28} aria-hidden="true" />
      </div>
      <div className="routeList">
        {result.alternatives.map((alternative, index) => (
          <RouteCard
            key={`${alternative.rank}-${alternative.legs[0]?.trip_id ?? index}`}
            alternative={alternative}
            labels={routeLabels(result, index)}
          />
        ))}
      </div>
      <details className="performanceDetails">
        <summary><BarChart3 size={17} /> Performance details</summary>
        <dl>
          <div><dt>Data loading</dt><dd>{result.timing.data_loading_ms.toFixed(2)} ms</dd></div>
          <div><dt>Route search</dt><dd>{result.timing.search_ms.toFixed(2)} ms</dd></div>
          <div><dt>Ranking</dt><dd>{result.timing.ranking_ms.toFixed(2)} ms</dd></div>
          <div><dt>Total API time</dt><dd><Clock size={14} /> {result.timing.total_ms.toFixed(2)} ms</dd></div>
        </dl>
      </details>
    </section>
  );
}
