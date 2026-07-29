import { useEffect, useRef, useState } from "react";
import { Database, GitCompareArrows, ShieldCheck } from "lucide-react";
import { ApiError, checkHealth, planRoutes } from "./api/client";
import type {
  ApiStatus,
  RoutePlanRequest,
  RoutePlanResponse
} from "./api/types";
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
  const planningController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal)
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("unavailable"));
    return () => controller.abort();
  }, []);

  useEffect(
    () => () => planningController.current?.abort(),
    []
  );

  const submit = async (request: RoutePlanRequest) => {
    planningController.current?.abort();
    const controller = new AbortController();
    planningController.current = controller;
    setLoading(true);
    setError(null);
    try {
      const nextResult = await planRoutes(request, controller.signal);
      setResult(nextResult);
      window.setTimeout(() => {
        document.getElementById("route-results")?.scrollIntoView({
          behavior: "smooth",
          block: "start"
        });
      }, 0);
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "An unexpected error occurred while planning your trip."
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
              Compare scheduled travel time with reliability estimated from
              historical transit observations.
            </p>
          </div>
          <div className="introProof" aria-label="Planner capabilities">
            <span><Database size={17} /> GTFS schedules</span>
            <span><ShieldCheck size={17} /> Historical reliability</span>
            <span><GitCompareArrows size={17} /> Ranked trade-offs</span>
          </div>
        </section>

        <div className="plannerLayout">
          <TripPlannerForm loading={loading} onSubmit={submit} />
          <TransitMap result={result} />
        </div>

        <PlanningStatus loading={loading} error={error} />
        {result && (
          <div id="route-results">
            <RouteResults result={result} />
          </div>
        )}

        <details className="engineDetails">
          <summary>How this ranking works</summary>
          <div className="engineGrid">
            <div><span>01</span><strong>Search schedules</strong><p>Available routes are found from the imported GTFS schedule for your selected service day.</p></div>
            <div><span>02</span><strong>Estimate reliability</strong><p>Aggregated historical delay profiles penalize service that runs materially early or late.</p></div>
            <div><span>03</span><strong>Combine the journey</strong><p>Multi-leg reliability is combined across every vehicle in the trip, not averaged.</p></div>
            <div><span>04</span><strong>Rank trade-offs</strong><p>Your priorities weight reliability and scheduled travel time. Broader fallback profiles are used when detailed data is sparse.</p></div>
          </div>
        </details>
      </main>
      <footer>
        <p>Built to make transit reliability visible—not to promise perfect service.</p>
        <a href="https://github.com/Ramtin-Mandom/Vancouver-Transit-Planner" target="_blank" rel="noreferrer">View the project on GitHub</a>
      </footer>
    </>
  );
}
