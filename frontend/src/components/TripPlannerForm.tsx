import { useState, type FormEvent } from "react";
import { ArrowDownUp, ChevronDown, LoaderCircle, Search } from "lucide-react";
import type { ApiStatus, RoutePlanRequest, Stop } from "../api/types";
import { PriorityControls } from "./PriorityControls";
import { StopAutocomplete } from "./StopAutocomplete";

export interface PlannerValues {
  origin: Stop | null;
  destination: Stop | null;
  departureTime: string;
  includeAlternatives: boolean;
  reliability: number;
  transferEffect: number;
  minimumSamples: number;
  maxExtraMinutes: number;
  searchTimeoutSeconds: number;
}

interface Props {
  loading: boolean;
  apiStatus?: ApiStatus;
  onSubmit: (request: RoutePlanRequest) => void;
}

type Errors = Partial<Record<"origin" | "destination" | "time", string>>;

export const DEFAULT_PLANNER_VALUES: PlannerValues = {
  origin: null,
  destination: null,
  departureTime: "08:00:00",
  includeAlternatives: false,
  reliability: 50,
  transferEffect: 0,
  minimumSamples: 10,
  maxExtraMinutes: 30,
  searchTimeoutSeconds: 30
};

export function TripPlannerForm({ loading, apiStatus = "connected", onSubmit }: Props) {
  const [values, setValues] = useState(DEFAULT_PLANNER_VALUES);
  const [errors, setErrors] = useState<Errors>({});

  const update = <K extends keyof PlannerValues>(key: K, value: PlannerValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextErrors: Errors = {};
    if (!values.origin) nextErrors.origin = "Select an origin from the suggestions.";
    if (!values.destination) {
      nextErrors.destination = "Select a destination from the suggestions.";
    }
    if (
      values.origin &&
      values.destination &&
      values.origin.stop_id === values.destination.stop_id
    ) {
      nextErrors.destination = "Origin and destination must be different.";
    }
    if (!/^\d+:[0-5]\d:[0-5]\d$/.test(values.departureTime)) {
      nextErrors.time = "Use GTFS time in HH:MM:SS format.";
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || !values.origin || !values.destination) {
      return;
    }
    onSubmit({
      origin_stop_id: values.origin.stop_id,
      destination_stop_id: values.destination.stop_id,
      departure_time: values.departureTime,
      include_alternatives: values.includeAlternatives,
      minimum_samples: values.minimumSamples,
      max_extra_minutes: values.maxExtraMinutes,
      search_timeout_seconds: values.searchTimeoutSeconds,
      reliability_effect: values.reliability / 100,
      travel_time_effect: (100 - values.reliability) / 100,
      transfer_effect: values.transferEffect / 100
    });
  };

  const swap = () => {
    setValues((current) => ({
      ...current,
      origin: current.destination,
      destination: current.origin
    }));
    setErrors({});
  };

  return (
    <form className="plannerPanel" id="planner" onSubmit={submit} noValidate>
      <div className="panelTitle">
        <div>
          <p className="eyebrow">Plan a trip</p>
          <h2>Where are you headed?</h2>
        </div>
        <span className="engineBadge">Historical reliability included</span>
      </div>

      <div className="stopFields">
        <StopAutocomplete
          id="origin-stop"
          label="Origin"
          placeholder="Search by stop name"
          value={values.origin}
          onChange={(stop) => update("origin", stop)}
          error={errors.origin}
          apiAvailable={apiStatus !== "unavailable"}
        />
        <button
          type="button"
          className="swapButton"
          aria-label="Swap origin and destination"
          onClick={swap}
        >
          <ArrowDownUp size={19} />
        </button>
        <StopAutocomplete
          id="destination-stop"
          label="Destination"
          placeholder="Search by stop name"
          value={values.destination}
          onChange={(stop) => update("destination", stop)}
          error={errors.destination}
          apiAvailable={apiStatus !== "unavailable"}
        />
      </div>

      <div className="tripFields">
        <div className="fieldGroup">
          <label htmlFor="travel-date">Travel date</label>
          <input id="travel-date" type="date" disabled aria-describedby="travel-date-status" />
          <small id="travel-date-status" className="unavailableStatus">
            Feature not implemented
          </small>
        </div>
        <div className="fieldGroup">
          <label htmlFor="departure-time">Departure time</label>
          <input
            id="departure-time"
            inputMode="numeric"
            value={values.departureTime}
            aria-invalid={Boolean(errors.time)}
            onChange={(event) => update("departureTime", event.target.value)}
          />
          {errors.time && <small className="fieldError">{errors.time}</small>}
        </div>
        <div className="alternativesOption">
          <label>
            <input
              type="checkbox"
              aria-label="Alternatives"
              checked={values.includeAlternatives}
              onChange={(event) => update("includeAlternatives", event.target.checked)}
            />
            <span>
              <strong>Alternatives</strong>
              <small>Find up to 3 routes (may take longer).</small>
            </span>
          </label>
        </div>
      </div>

      <PriorityControls
        reliability={values.reliability}
        onChange={(value) => update("reliability", value)}
      />

      <details className="advancedOptions">
        <summary>
          <ChevronDown size={17} /> Advanced options
        </summary>
        <div className="advancedGrid">
          <div className="fieldGroup">
            <label htmlFor="transfer-effect">Transfer priority (%)</label>
            <input
              id="transfer-effect"
              type="number"
              min="0"
              max="100"
              value={values.transferEffect}
              onChange={(event) => update("transferEffect", Number(event.target.value))}
            />
            <small>Optionally prefer journeys with fewer transfers.</small>
          </div>
          <div className="fieldGroup">
            <label htmlFor="minimum-samples">Minimum samples</label>
            <input
              id="minimum-samples"
              type="number"
              min="1"
              value={values.minimumSamples}
              onChange={(event) =>
                update("minimumSamples", Math.max(1, Number(event.target.value)))
              }
            />
            <small>Threshold for marking reliability data as sufficient.</small>
          </div>
          <div className="fieldGroup">
            <label htmlFor="extra-minutes">Maximum extra minutes</label>
            <input
              id="extra-minutes"
              type="number"
              min="0"
              max="120"
              value={values.maxExtraMinutes}
              onChange={(event) => update("maxExtraMinutes", Number(event.target.value))}
            />
            <small>How much slower an alternative may be.</small>
          </div>
          <div className="fieldGroup">
            <label htmlFor="search-timeout">Search timeout (seconds)</label>
            <input
              id="search-timeout"
              type="number"
              min="1"
              max="120"
              step="1"
              value={values.searchTimeoutSeconds}
              onChange={(event) => update("searchTimeoutSeconds", Number(event.target.value))}
            />
            <small>Stops unusually expensive searches after 1–120 seconds.</small>
          </div>
        </div>
      </details>

      <button className="primaryButton" type="submit" disabled={loading}>
        {loading ? <LoaderCircle className="spin" size={19} /> : <Search size={19} />}
        {loading ? "Comparing routes…" : "Find routes"}
      </button>
    </form>
  );
}
