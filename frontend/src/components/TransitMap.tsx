import { useEffect } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { MapPinned } from "lucide-react";
import type { RoutePlanResponse, Stop } from "../api/types";
import "leaflet/dist/leaflet.css";

function hasCoordinates(stop: Stop): stop is Stop & { latitude: number; longitude: number } {
  return stop.latitude !== null && stop.longitude !== null;
}

function FitPoints({ points }: { points: Array<[number, number]> }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 1) map.setView(points[0], 14);
    if (points.length > 1) {
      map.fitBounds(points as LatLngBoundsExpression, { padding: [28, 28] });
    }
  }, [map, points]);
  return null;
}

export function TransitMap({ result }: { result: RoutePlanResponse | null }) {
  const stops = result
    ? [
        result.origin,
        ...result.alternatives.flatMap((alternative) =>
          alternative.legs.flatMap((leg) => [leg.origin, leg.destination])
        ),
        result.destination
      ]
    : [];
  const unique = Array.from(
    new Map(stops.filter(hasCoordinates).map((stop) => [stop.stop_id, stop])).values()
  );
  const points = unique.map((stop) => [stop.latitude, stop.longitude] as [number, number]);

  if (points.length === 0) {
    return (
      <section className="mapPlaceholder" aria-label="Trip map">
        <MapPinned size={30} />
        <div>
          <strong>{result ? "Map coordinates unavailable" : "Your trip at a glance"}</strong>
          <p>
            {result
              ? "The route remains fully available in the itinerary."
              : "Select two stops to see their locations here."}
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="mapPanel" aria-label="Trip stop map">
      <MapContainer center={points[0]} zoom={12} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitPoints points={points} />
        {unique.map((stop, index) => (
          <CircleMarker
            key={stop.stop_id}
            center={[stop.latitude, stop.longitude]}
            radius={index === 0 || index === unique.length - 1 ? 9 : 6}
            pathOptions={{
              color: "#ffffff",
              weight: 3,
              fillColor: index === 0 ? "#087f77" : index === unique.length - 1 ? "#e19a2b" : "#315b73",
              fillOpacity: 1
            }}
          >
            <Popup>{stop.stop_name}</Popup>
          </CircleMarker>
        ))}
      </MapContainer>
      <p className="mapCaption">
        Stop locations only. The API does not return route geometry, so no path is drawn.
      </p>
    </section>
  );
}
