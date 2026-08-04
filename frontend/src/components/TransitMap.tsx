import { Fragment, useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { MapPinned } from "lucide-react";
import type { RouteAlternative, RoutePlanResponse, Stop } from "../api/types";
import "leaflet/dist/leaflet.css";

const ALTERNATIVE_COLORS = ["#087f77", "#315b73", "#b66a18"];

function hasCoordinates(stop: Stop): stop is Stop & { latitude: number; longitude: number } {
  return Number.isFinite(stop.latitude) && Number.isFinite(stop.longitude);
}

function orderedStops(alternative: RouteAlternative): Stop[] {
  const stops = alternative.legs.flatMap((leg) =>
    leg.stops.length > 0 ? leg.stops.map((item) => item.stop) : [leg.origin, leg.destination]
  );
  return stops.filter((stop, index) => index === 0 || stop.stop_id !== stops[index - 1].stop_id);
}

function FitPoints({ points }: { points: Array<[number, number]> }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 14);
    if (points.length > 1) map.fitBounds(points as LatLngBoundsExpression, { padding: [28, 28] });
  }, [map, points]);
  return null;
}

interface Props {
  result: RoutePlanResponse | null;
  selectedRank: number;
  onSelect: (rank: number) => void;
}

export function TransitMap({ result, selectedRank, onSelect }: Props) {
  const [tilesFailed, setTilesFailed] = useState(false);
  const routes = useMemo(
    () =>
      (result?.alternatives ?? []).map((alternative, index) => ({
        alternative,
        color: ALTERNATIVE_COLORS[index % ALTERNATIVE_COLORS.length],
        stops: orderedStops(alternative),
        points: orderedStops(alternative)
          .filter(hasCoordinates)
          .map((stop) => [stop.latitude, stop.longitude] as [number, number])
      })),
    [result]
  );
  const selected = routes.find((route) => route.alternative.rank === selectedRank) ?? routes[0];
  const fitPoints = selected?.points ?? [];

  if (routes.length === 0 || routes.every((route) => route.points.length === 0)) {
    return (
      <section className="mapPlaceholder" aria-label="Trip map">
        <MapPinned size={30} />
        <div>
          <strong>{result ? "Map coordinates unavailable" : "Your trip at a glance"}</strong>
          <p>
            {result
              ? "The itinerary remains available below."
              : "Select two stops to see their locations here."}
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="mapPanel" aria-label="Trip alternatives map">
      {routes.length > 1 && (
        <div className="mapLegend" aria-label="Choose route alternative">
          {routes.map(({ alternative, color }) => (
            <button
              type="button"
              key={alternative.rank}
              aria-pressed={alternative.rank === selectedRank}
              onClick={() => onSelect(alternative.rank)}
            >
              <i style={{ backgroundColor: color }} aria-hidden="true" /> Route {alternative.rank}
            </button>
          ))}
        </div>
      )}
      <MapContainer
        center={fitPoints[0] ?? routes.flatMap((route) => route.points)[0]}
        zoom={12}
        scrollWheelZoom={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          eventHandlers={{ tileerror: () => setTilesFailed(true) }}
        />
        <FitPoints points={fitPoints} />
        {routes.map(({ alternative, color, stops, points }) => {
          const emphasized = alternative.rank === selectedRank;
          const coordinateStops = stops.filter(hasCoordinates);
          return (
            <Fragment key={alternative.rank}>
              {points.length > 1 && (
                <Polyline
                  positions={points}
                  pathOptions={{
                    color,
                    weight: emphasized ? 6 : 3,
                    opacity: emphasized ? 0.95 : 0.35
                  }}
                />
              )}
              {coordinateStops.map((stop, index) => {
                const endpoint =
                  index === 0
                    ? "origin"
                    : index === coordinateStops.length - 1
                      ? "destination"
                      : "stop";
                return (
                  <CircleMarker
                    key={`${alternative.rank}-${stop.stop_id}-${index}`}
                    center={[stop.latitude, stop.longitude]}
                    radius={endpoint === "stop" ? (emphasized ? 5 : 3) : emphasized ? 9 : 6}
                    pathOptions={{
                      color: "#ffffff",
                      weight: emphasized ? 3 : 2,
                      fillColor:
                        endpoint === "origin"
                          ? "#087f77"
                          : endpoint === "destination"
                            ? "#e19a2b"
                            : color,
                      fillOpacity: emphasized ? 1 : 0.65
                    }}
                  >
                    <Popup>{`Route ${alternative.rank} ${endpoint}: ${stop.stop_name}`}</Popup>
                  </CircleMarker>
                );
              })}
            </Fragment>
          );
        })}
      </MapContainer>
      {tilesFailed && (
        <p className="tileError" role="status">
          Map tiles could not be loaded. Route stops are still listed below.
        </p>
      )}
      <p className="mapCaption">
        Lines connect scheduled stops; they are not street-level vehicle geometry.
      </p>
    </section>
  );
}
