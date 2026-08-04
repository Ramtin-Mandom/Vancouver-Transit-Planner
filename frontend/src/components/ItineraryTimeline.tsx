import { useId, useState } from "react";
import { ArrowDown, BusFront, ChevronDown, Clock3, Route } from "lucide-react";
import type { LegStop, RouteLeg } from "../api/types";

function secondsToDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours} hr ${minutes} min` : `${minutes} min`;
}

export function ItineraryTimeline({ legs }: { legs: RouteLeg[] }) {
  const disclosureId = useId();
  const [expandedLegs, setExpandedLegs] = useState<Set<string>>(new Set());

  const legKey = (leg: RouteLeg) =>
    `${leg.trip_id}-${leg.stops[0]?.stop_sequence ?? "start"}-${leg.stops.at(-1)?.stop_sequence ?? "end"}`;

  const displayTime = (item: LegStop, position: number, total: number) => {
    if (position === 0) return item.departure_time ?? item.arrival_time;
    if (position === total - 1) return item.arrival_time ?? item.departure_time;
    return item.arrival_time ?? item.departure_time;
  };

  const toggleLeg = (key: string) => {
    setExpandedLegs((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <ol className="itineraryTimeline" aria-label="Trip itinerary">
      {legs.map((leg, index) => {
        const key = legKey(leg);
        const expanded = expandedLegs.has(key);
        const intermediateCount = Math.max(0, leg.stops.length - 2);
        const stopsId = `${disclosureId}-${key}`.replace(/[^a-zA-Z0-9_-]/g, "-");
        return (
          <li key={key} className="timelineLeg">
            <div className="timelineRail" aria-hidden="true">
              <span>
                <BusFront size={15} />
              </span>
              {index < legs.length - 1 && <i />}
            </div>
            <div className="legContent">
              <div className="legHeading">
                <span className="routeToken">
                  <Route size={14} /> {leg.route_name}
                </span>
                <span className="legDuration">
                  <Clock3 size={14} /> {secondsToDuration(leg.duration_seconds)}
                </span>
              </div>
              <div className="legStops">
                <div>
                  <time>{leg.departure_time}</time>
                  <strong>{leg.origin.stop_name}</strong>
                </div>
                <ArrowDown size={16} aria-hidden="true" />
                <div>
                  <time>{leg.arrival_time}</time>
                  <strong>{leg.destination.stop_name}</strong>
                </div>
              </div>
              {intermediateCount > 0 && (
                <>
                  <button
                    type="button"
                    className="stopDisclosure"
                    aria-expanded={expanded}
                    aria-controls={stopsId}
                    onClick={() => toggleLeg(key)}
                  >
                    {expanded ? "Hide" : "View"} {intermediateCount} intermediate{" "}
                    {intermediateCount === 1 ? "stop" : "stops"}
                    <ChevronDown
                      className={expanded ? "chevronUp" : ""}
                      size={15}
                      aria-hidden="true"
                    />
                  </button>
                  {expanded && (
                    <ol
                      id={stopsId}
                      className="legStopList"
                      aria-label={`${leg.route_name} scheduled stops`}
                    >
                      {leg.stops.map((item, stopIndex) => (
                        <li
                          key={`${leg.trip_id}-${item.stop_sequence}`}
                          className={
                            stopIndex === 0 || stopIndex === leg.stops.length - 1
                              ? "legStopBoundary"
                              : undefined
                          }
                        >
                          <time>
                            {displayTime(item, stopIndex, leg.stops.length) ?? "Time unavailable"}
                          </time>
                          <span>{item.stop.stop_name}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}
              <details className="technicalDetails">
                <summary>Technical details</summary>
                <dl>
                  <div>
                    <dt>Trip ID</dt>
                    <dd>{leg.trip_id}</dd>
                  </div>
                  <div>
                    <dt>Route ID</dt>
                    <dd>{leg.route_id}</dd>
                  </div>
                  <div>
                    <dt>Direction</dt>
                    <dd>{leg.direction_id ?? "Not provided"}</dd>
                  </div>
                </dl>
              </details>
              {index < legs.length - 1 && (
                <p className="transferNote">Transfer at {leg.destination.stop_name}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
