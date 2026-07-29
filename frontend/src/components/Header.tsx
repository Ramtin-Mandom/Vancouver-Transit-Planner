import { Github, Radio } from "lucide-react";
import type { ApiStatus } from "../api/types";

const statusText: Record<ApiStatus, string> = {
  checking: "Checking API",
  connected: "API connected",
  unavailable: "API unavailable"
};

export function Header({ status }: { status: ApiStatus }) {
  return (
    <header className="siteHeader">
      <div className="headerInner">
        <a className="brand" href="#planner" aria-label="Vancouver Transit Planner home">
          <span className="brandMark" aria-hidden="true">V</span>
          <span>
            <strong>Vancouver Transit Planner</strong>
            <small>Reliability-aware routing</small>
          </span>
        </a>
        <div className="headerActions">
          <span className={`apiStatus apiStatus--${status}`} role="status">
            <Radio size={14} aria-hidden="true" />
            {statusText[status]}
          </span>
          <a
            className="githubLink"
            href="https://github.com/Ramtin-Mandom/Vancouver-Transit-Planner"
            target="_blank"
            rel="noreferrer"
          >
            <Github size={18} aria-hidden="true" />
            <span>GitHub</span>
          </a>
        </div>
      </div>
    </header>
  );
}
