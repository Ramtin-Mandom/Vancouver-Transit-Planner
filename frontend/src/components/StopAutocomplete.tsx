import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { LoaderCircle, MapPin, Search, X } from "lucide-react";
import { ApiError, searchStops } from "../api/client";
import type { Stop } from "../api/types";

interface Props {
  id: string;
  label: string;
  placeholder: string;
  value: Stop | null;
  onChange: (stop: Stop | null) => void;
  error?: string;
  apiAvailable?: boolean;
}

export function StopAutocomplete({
  id,
  label,
  placeholder,
  value,
  onChange,
  error,
  apiAvailable = true
}: Props) {
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const [text, setText] = useState(value?.stop_name ?? "");
  const [results, setResults] = useState<Stop[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [searchState, setSearchState] = useState<
    "idle" | "loading" | "empty" | "error" | "unavailable"
  >("idle");

  useEffect(() => {
    setText(value?.stop_name ?? "");
  }, [value]);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  useEffect(() => {
    const query = text.trim();
    if (value || query.length < 2) {
      setResults([]);
      setLoading(false);
      setSearchState("idle");
      return;
    }
    const controller = new AbortController();
    const currentRequest = ++requestId.current;
    const timeout = window.setTimeout(async () => {
      setLoading(true);
      setSearchState(apiAvailable ? "loading" : "unavailable");
      setOpen(true);
      if (!apiAvailable) {
        setLoading(false);
        return;
      }
      try {
        const stops = await searchStops(query, 10, controller.signal);
        if (currentRequest === requestId.current) {
          setResults(stops);
          setSearchState(stops.length === 0 ? "empty" : "idle");
          setActiveIndex(-1);
        }
      } catch (searchError) {
        if (!(searchError instanceof DOMException && searchError.name === "AbortError")) {
          if (currentRequest === requestId.current) {
            setResults([]);
            setSearchState(
              searchError instanceof ApiError && searchError.kind === "planner_not_ready"
                ? "unavailable"
                : "error"
            );
          }
        }
      } finally {
        if (currentRequest === requestId.current) setLoading(false);
      }
    }, 300);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [apiAvailable, text, value]);

  const select = (stop: Stop) => {
    onChange(stop);
    setText(stop.stop_name);
    setOpen(false);
    setResults([]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) {
      if (event.key === "ArrowDown" && text.trim().length >= 2) setOpen(true);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      select(results[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="fieldGroup autocomplete" ref={rootRef}>
      <label htmlFor={id}>{label}</label>
      <div className={`inputShell ${error ? "inputShell--error" : ""}`}>
        <Search size={18} aria-hidden="true" />
        <input
          id={id}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={activeIndex >= 0 ? `${listboxId}-${activeIndex}` : undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          autoComplete="off"
          placeholder={placeholder}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            if (value) onChange(null);
            setOpen(event.target.value.trim().length >= 2);
          }}
          onFocus={() => {
            if (!value && text.trim().length >= 2) setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        {loading && <LoaderCircle className="spin" size={18} aria-label="Searching stops" />}
        {(value || text) && !loading && (
          <button
            type="button"
            className="iconButton iconButton--small"
            aria-label={`Clear ${label.toLowerCase()}`}
            onClick={() => {
              setText("");
              onChange(null);
              setResults([]);
            }}
          >
            <X size={16} />
          </button>
        )}
      </div>
      {error && (
        <small className="fieldError" id={`${id}-error`}>
          {error}
        </small>
      )}
      {open && !value && (
        <div className="autocompleteMenu">
          <ul id={listboxId} role="listbox" aria-label={`${label} suggestions`}>
            {!loading && results.length === 0 && (
              <li className="emptyOption">
                {text.trim().length < 2 && "Type at least 2 characters"}
                {text.trim().length >= 2 && searchState === "empty" && "No matching stops found"}
                {searchState === "error" &&
                  "Stop search failed. Check your connection and try again."}
                {searchState === "unavailable" && "Planner is currently unavailable."}
                {searchState === "loading" && "Searching stops…"}
              </li>
            )}
            {results.map((stop, index) => (
              <li
                id={`${listboxId}-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                key={stop.stop_id}
                className={index === activeIndex ? "activeOption" : ""}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(stop)}
              >
                <MapPin size={17} aria-hidden="true" />
                <span>
                  <strong>{stop.stop_name}</strong>
                  <small>
                    {stop.stop_code ? `Stop ${stop.stop_code} · ` : ""}
                    ID {stop.stop_id}
                  </small>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
