import { ArrowDown, BusFront, Clock3, Route } from "lucide-react";
import type { RouteLeg } from "../api/types";

function secondsToDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours} hr ${minutes} min` : `${minutes} min`;
}

export function ItineraryTimeline({ legs }: { legs: RouteLeg[] }) {
  return (
    <ol className="itineraryTimeline" aria-label="Trip itinerary">
      {legs.map((leg, index) => (
        <li key={`${leg.trip_id}-${index}`} className="timelineLeg">
          <div className="timelineRail" aria-hidden="true">
            <span><BusFront size={15} /></span>
            {index < legs.length - 1 && <i />}
          </div>
          <div className="legContent">
            <div className="legHeading">
              <span className="routeToken"><Route size={14} /> {leg.route_name}</span>
              <span className="legDuration"><Clock3 size={14} /> {secondsToDuration(leg.duration_seconds)}</span>
            </div>
            <div className="legStops">
              <div><time>{leg.departure_time}</time><strong>{leg.origin.stop_name}</strong></div>
              <ArrowDown size={16} aria-hidden="true" />
              <div><time>{leg.arrival_time}</time><strong>{leg.destination.stop_name}</strong></div>
            </div>
            <details className="technicalDetails">
              <summary>Technical details</summary>
              <dl>
                <div><dt>Trip ID</dt><dd>{leg.trip_id}</dd></div>
                <div><dt>Route ID</dt><dd>{leg.route_id}</dd></div>
                <div><dt>Direction</dt><dd>{leg.direction_id ?? "Not provided"}</dd></div>
              </dl>
            </details>
            {index < legs.length - 1 && (
              <p className="transferNote">Transfer at {leg.destination.stop_name}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
