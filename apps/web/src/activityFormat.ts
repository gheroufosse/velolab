/** Presentation only: units/metrics are owned by the backend (ADR-025). */
const validMetric = (value: number | null): value is number =>
  value !== null && Number.isFinite(value) && value >= 0;

export function formatDuration(seconds: number | null): string {
  if (!validMetric(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

export function formatDistance(metres: number | null): string {
  return validMetric(metres) ? String(Math.round(metres / 100) / 10) : "—";
}

export function formatLoad(load: number | null): string {
  return validMetric(load) ? String(load) : "—";
}

export function formatLocalStart(start: string | null): string {
  // Never use Date: this is the provider's wall clock, not a browser-zone instant.
  return start && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(start)
    ? start.replace("T", " ") : "—";
}
