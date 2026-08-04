import { useEffect, useRef, useState } from "react";
import { Database, GitCompareArrows, ShieldCheck } from "lucide-react";
import { ApiError, checkReady, planRoutes } from "./api/client";
import type { ApiErrorKind } from "./api/client";
import type { ApiStatus, RoutePlanRequest, RoutePlanResponse } from "./api/types";
import { Header } from "./components/Header";
import { RouteResults } from "./components/RouteResults";
import { PlanningStatus } from "./components/StatusMessage";
import { TransitMap } from "./components/TransitMap";
import { TripPlannerForm } from "./components/TripPlannerForm";

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [result, setResult] = useState<RoutePlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedAlternative, setSelectedAlternative] = useState(1);
  const planningController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    checkReady(controller.signal)
      .then((status) => setApiStatus(status.ready ? "connected" : "unavailable"))
      .catch(() => setApiStatus("unavailable"));
    return () => controller.abort();
  }, []);

  useEffect(() => () => planningController.current?.abort(), []);

  const submit = async (request: RoutePlanRequest) => {
    planningController.current?.abort();
    const controller = new AbortController();
    planningController.current = controller;
    setLoading(true);
    setError(null);
    setResult(null);
    setSelectedAlternative(1);
    try {
      const nextResult = await planRoutes(request, controller.signal);
      if (planningController.current !== controller) return;
      setResult(nextResult);
      setSelectedAlternative(nextResult.alternatives[0]?.rank ?? 1);
      window.setTimeout(() => {
        document.getElementById("route-results")?.scrollIntoView({
          behavior: "smooth",
          block: "start"
        });
      }, 0);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      if (planningController.current !== controller) return;
      setResult(null);
      const messages: Partial<Record<ApiErrorKind, string>> = {
        invalid_input: "Review the trip details and try again.",
        timeout: "The route search timed out. Try again with fewer alternatives.",
        planner_not_ready: "The planner is not ready yet. Please try again shortly.",
        feed_expired:
          "Transit schedule data has expired. Routes are unavailable until it is refreshed.",
        network: "Unable to reach the planner. Check your connection and try again."
      };
      setError(
        requestError instanceof ApiError
          ? (messages[requestError.kind] ?? "The planner could not complete this request.")
          : "The planner could not complete this request."
      );
    } finally {
      if (planningController.current === controller) setLoading(false);
    }
  };

  return (
    <>
      <Header status={apiStatus} />
      <main>
        <section className="intro">
          <div>
            <p className="eyebrow">Smarter scheduled transit</p>
            <h1>Choose a route that is fast—and likely to be on time.</h1>
            <p>
              Compare scheduled travel time with reliability estimated from historical transit
              observations.
            </p>
          </div>
          <div className="introProof" aria-label="Planner capabilities">
            <span>
              <Database size={17} /> GTFS schedules
            </span>
            <span>
              <ShieldCheck size={17} /> Historical reliability
            </span>
            <span>
              <GitCompareArrows size={17} /> Ranked trade-offs
            </span>
          </div>
        </section>

        <div className="plannerLayout">
          <TripPlannerForm loading={loading} apiStatus={apiStatus} onSubmit={submit} />
          <TransitMap
            result={result}
            selectedRank={selectedAlternative}
            onSelect={setSelectedAlternative}
          />
        </div>

        <PlanningStatus loading={loading} error={error} />
        {result && (
          <div id="route-results">
            <RouteResults
              result={result}
              selectedRank={selectedAlternative}
              onSelect={setSelectedAlternative}
            />
          </div>
        )}

        <details className="engineDetails">
          <summary>How this ranking works</summary>
          <div className="engineGrid">
            <div>
              <span>01</span>
              <strong>Search schedules</strong>
              <p>
                Available routes are found from the imported GTFS schedule for your selected service
                day.
              </p>
            </div>
            <div>
              <span>02</span>
              <strong>Estimate reliability</strong>
              <p>
                Aggregated historical delay profiles penalize service that runs materially early or
                late.
              </p>
            </div>
            <div>
              <span>03</span>
              <strong>Combine the journey</strong>
              <p>
                Multi-leg reliability is combined across every vehicle in the trip, not averaged.
              </p>
            </div>
            <div>
              <span>04</span>
              <strong>Rank trade-offs</strong>
              <p>
                Your priorities weight reliability and scheduled travel time. Reliability profile
                details come directly from the planner response.
              </p>
            </div>
          </div>
        </details>
      </main>
      <footer>
        <p>Built to make transit reliability visible—not to promise perfect service.</p>
        <a
          href="https://github.com/Ramtin-Mandom/Vancouver-Transit-Planner"
          target="_blank"
          rel="noreferrer"
        >
          View the project on GitHub
        </a>
      </footer>
    </>
  );
}
