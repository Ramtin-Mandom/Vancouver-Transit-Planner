import { AlertCircle, LoaderCircle } from "lucide-react";

export function PlanningStatus({
  loading,
  error
}: {
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <section className="loadingState" aria-live="polite">
        <LoaderCircle className="spin" size={24} />
        <div><strong>Comparing scheduled routes</strong><p>Applying your reliability and travel-time priorities…</p></div>
      </section>
    );
  }
  if (error) {
    return (
      <section className="errorState" role="alert">
        <AlertCircle size={22} />
        <div><strong>We couldn’t plan that trip</strong><p>{error}</p></div>
      </section>
    );
  }
  return null;
}
