import { useEffect, useState } from "react";
import { Star } from "lucide-react";
import { useTheme } from "./ThemeContext";

export function DeliveryReviewMetrics() {
  const { t } = useTheme();
  const [data, setData] = useState<{ rated: number; average_rating: number | null; confirmed: number; open: number; outstanding_issues: number } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => { try { const response = await fetch("/api/customer-support/delivery-review-summary", { credentials: "same-origin", signal: controller.signal }); const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.message || "Unable to load delivery ratings."); setData(result.summary); setError(""); } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Unable to load delivery ratings."); } };
    void load(); window.addEventListener("customer-support-delivery-reviews-changed", load);
    return () => { controller.abort(); window.removeEventListener("customer-support-delivery-reviews-changed", load); };
  }, []);
  return <section className="rounded-xl border p-5" style={{ background: t.surface, borderColor: t.border, color: t.textPrimary }}><h2 className="flex items-center gap-2 font-bold"><Star size={19} style={{ color: t.accent }} />Delivery satisfaction</h2><p className="mb-4 mt-1 text-xs" style={{ color: t.textTertiary }}>Customer ratings and receipt confirmations from delivery follow-ups. Each card uses its latest review.</p>{error ? <p role="alert" style={{ color: t.dangerText }}>{error}</p> : !data ? <p>Loading delivery satisfaction…</p> : <div className="grid grid-cols-2 gap-4 md:grid-cols-4">{[["Average rating", data.average_rating === null ? "Not rated" : `${data.average_rating} / 5`, `${data.rated} rated cards`], ["Receipt confirmed", data.confirmed, "Full receipt, issues resolved"], ["Open follow-ups", data.open, "Awaiting customer confirmation"], ["Outstanding issues", data.outstanding_issues, "Require further action"]].map(([label,value,subtitle]) => <article key={label} className="rounded-lg border p-4" style={{ borderColor: t.border }}><strong className="text-2xl">{value}</strong><h3 className="mt-2 text-sm font-semibold">{label}</h3><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>{subtitle}</p></article>)}</div>}</section>;
}
