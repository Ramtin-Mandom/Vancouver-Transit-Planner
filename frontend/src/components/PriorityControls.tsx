import { RotateCcw, ShieldCheck, Timer } from "lucide-react";

interface Props {
  reliability: number;
  onChange: (reliability: number) => void;
}

export function PriorityControls({ reliability, onChange }: Props) {
  const travelTime = 100 - reliability;
  return (
    <section className="prioritySection" aria-labelledby="priority-heading">
      <div className="sectionHeading">
        <div>
          <p className="eyebrow">Routing priorities</p>
          <h3 id="priority-heading">Balance dependable and fast</h3>
        </div>
        <button type="button" className="textButton" onClick={() => onChange(50)}>
          <RotateCcw size={14} aria-hidden="true" /> Reset priorities
        </button>
      </div>
      <div className="priorityValues" aria-live="polite">
        <span>
          <ShieldCheck size={17} /> Reliability <strong>{reliability}%</strong>
        </span>
        <span>
          <Timer size={17} /> Travel time <strong>{travelTime}%</strong>
        </span>
      </div>
      <input
        className="balanceSlider"
        type="range"
        min="0"
        max="100"
        step="5"
        value={reliability}
        aria-label="Reliability priority percentage"
        onChange={(event) => onChange(Number(event.target.value))}
        style={{ "--balance": `${reliability}%` } as React.CSSProperties}
      />
      <div className="sliderLabels">
        <span>Faster</span>
        <span>More dependable</span>
      </div>
      <p className="helperText">
        Increasing reliability may favor a slower route with stronger historical performance.
        Reliability is an estimate, not a guarantee.
      </p>
    </section>
  );
}
