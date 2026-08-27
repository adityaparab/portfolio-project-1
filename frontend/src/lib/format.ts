/** Presentation-only formatters (no business logic — AGENTS.md). */

/** Money arrives as Decimal-safe strings; keep them exact. */
export function formatMoney(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatAging(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value == null) return "—";
  return value.toFixed(2);
}
