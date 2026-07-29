import { useState } from "react";
import {
  AlertTriangle,
  Award,
  ChevronDown,
  Clock3,
  Gauge,
  ShieldCheck
} from "lucide-react";
import type { RouteAlternative } from "../api/types";
import { ItineraryTimeline } from "./ItineraryTimeline";

interface Props {
  alternative: RouteAlternative;
  labels: string[];
}

const fallbackLabels: Record<string, string> = {
  route_direction_window: "Route, direction & time-specific profile",
  route_direction: "Route and direction profile",
  route: "Route-wide profile",
  network: "Network-wide profile",
  "insufficient-data": "Insufficient historical data"
};

export function RouteCard({ alternative, labels }: Props) {
  const [expanded, setExpanded] = useState(alternative.rank === 1);
  const routeNames = [...new Set(alternative.legs.map((leg) => leg.route_name))];
  return (
    <article className={`routeCard ${alternative.rank === 1 ? "routeCard--best" : ""}`}>
      <div className="routeCardTop">
        <div className="rankBlock">
          <span>Rank</span>
          <strong>{alternative.rank}</strong>
        </div>
        <div className="routeIdentity">
          <div className="resultLabels">
            {labels.map((label) => (
              <span key={label}><Award size={13} /> {label}</span>
            ))}
          </div>
          <h3>{routeNames.length > 0 ? routeNames.join(" → ") : "Transit route"}</h3>
          <p>
            <time>{alternative.departure_time}</time>
            <span aria-hidden="true">→</span>
            <time>{alternative.arrival_time}</time>
          </p>
        </div>
        <div
          className="scoreBlock"
          title="The backend combines your selected reliability and travel-time priorities."
        >
          <span>Combined score</span>
          <strong>{alternative.combined_score.toFixed(1)}</strong>
          <small>out of 100</small>
        </div>
      </div>

      <div className="routeMetrics">
        <div><Clock3 size={18} /><span><small>Duration</small><strong>{alternative.duration_display}</strong></span></div>
        <div><Gauge size={18} /><span><small>Transfers</small><strong>{alternative.transfer_count}</strong></span></div>
        <div className="reliabilityMetric">
          <ShieldCheck size={18} />
          <span><small>Journey reliability</small><strong>{Math.round(alternative.route_reliability * 100)}%</strong></span>
        </div>
      </div>

      <p className="reliabilityNote">
        Reliability combines historical estimates across the complete journey;
        it is not a guarantee.
      </p>

      {alternative.insufficient_data && (
        <div className="dataWarning" role="status">
          <AlertTriangle size={17} />
          Limited historical data—one or more broader fallback profiles were used.
        </div>
      )}

      <button
        className="expandButton"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {expanded ? "Hide itinerary" : "View itinerary"}
        <ChevronDown className={expanded ? "chevronUp" : ""} size={18} />
      </button>

      {expanded && (
        <div className="routeDetails">
          <ItineraryTimeline legs={alternative.legs} />
          <details className="profileDetails">
            <summary>Reliability profile details</summary>
            <ul>
              {alternative.fallback_levels.map((level, index) => (
                <li key={`${level}-${index}`}>
                  <span>{fallbackLabels[level] ?? "Fallback profile"}</span>
                  <code>{level}</code>
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </article>
  );
}
