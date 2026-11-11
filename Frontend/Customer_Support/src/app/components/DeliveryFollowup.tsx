import { RecordAttribution, type SupportAttribution } from "./RecordAttribution";
import { useEffect, useState, type FormEvent } from "react";
import { CheckCircle2, PhoneCall, RefreshCw, Star, X } from "lucide-react";
import { useTheme } from "./ThemeContext";
import { RepeatPurchaseFields, type RepeatRequest } from "./RepeatPurchaseFields";
import { type DirectoryOption } from "./DirectorySelect";

type Review = SupportAttribution & { contacted: boolean; receipt: string; satisfaction: string; rating: number | null; issue: string; notes: string; next_followup_date: string; officer_name: string; recorded_at: string };
type Followup = SupportAttribution & { id: string; task_no: string; customer_name: string; customer_phone: string; product_name: string; branch: string; status: string; due_date: string; triggers: string[]; version: number; review: Review | null; history: Review[]; repeat_purchase?: { requested_product: string; agent_name: string; status: string } | null };
const statusLabel = (status: string) => status === "completed" ? "Confirmed" : status === "in progress" ? "Follow-up needed" : "Awaiting confirmation";
const receiptLabel: Record<string, string> = { full: "Fully received", partial: "Partly received", none: "Not received", unknown: "Not confirmed" };
export function RatingStars({ value }: { value: number | null }) { return <span className="inline-flex items-center gap-0.5" aria-label={value ? `${value} out of 5 stars` : "Not rated"}>{[1, 2, 3, 4, 5].map(star => <Star key={star} size={14} className={value && star <= value ? "fill-amber-400 text-amber-400" : "text-gray-300"} />)}</span>; }

export function DeliveryFollowupModal({ taskId, onClose, onSaved }: { taskId: string; onClose: () => void; onSaved: () => void }) {
  const { t } = useTheme();
  const [task, setTask] = useState<Followup | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [repeat, setRepeat] = useState<RepeatRequest>({ repeat_interest: "", requested_product: "", repeat_followup_date: "", repeat_notes: "" });
  const [repeatAgent, setRepeatAgent] = useState<DirectoryOption | null>(null);
  const [form, setForm] = useState({ contacted: true, receipt: "", satisfaction: "", rating: 0, issue: "", notes: "", next_followup_date: "" });
  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/customer-support/delivery-followups/${taskId}`, { credentials: "same-origin", signal: controller.signal })
      .then(async response => { const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load follow-up."); setTask(data.followup); const review = data.followup.review; if (review) setForm({ contacted: review.contacted, receipt: review.receipt === "unknown" ? "" : review.receipt, satisfaction: review.satisfaction === "unknown" ? "" : review.satisfaction, rating: review.rating || 0, issue: review.issue, notes: "", next_followup_date: review.next_followup_date || "" }); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [taskId]);
  const complete = form.contacted && form.receipt === "full" && form.satisfaction === "satisfied" && !form.issue.trim();
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!task) return; setError("");
    if (form.contacted && !form.rating) { setError("Select a star rating from 1 to 5."); return; }
    if (complete && !task.repeat_purchase && repeat.repeat_interest === "yes" && !repeatAgent) { setError("Select an agent for the repeat-purchase opportunity."); return; }
    setSaving(true);
    try {
      const response = await fetch(`/api/customer-support/delivery-followups/${taskId}`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, ...repeat, repeat_agent_id: repeatAgent?.id, version: task.version }) });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to save review.");
      window.dispatchEvent(new Event("customer-support-delivery-reviews-changed")); onSaved(); onClose();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to save review."); }
    finally { setSaving(false); }
  };
  const card = { background: t.surface, color: t.textPrimary, borderColor: t.border };
  const input = "mt-1.5 block w-full rounded-lg border px-3 py-2.5 text-sm";
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"><form onSubmit={save} role="dialog" aria-modal="true" aria-labelledby="delivery-review-title" className="max-h-[92vh] w-full max-w-2xl overflow-auto rounded-2xl border p-6 shadow-2xl" style={card}><div className="flex items-center justify-between"><h2 id="delivery-review-title" className="text-lg font-bold">Delivery confirmation & satisfaction</h2><button type="button" disabled={saving} aria-label="Close review" onClick={onClose}><X size={20} /></button></div>{!task ? <p className="mt-5">{error || "Loading follow-up…"}</p> : <>
    <div className="my-5 rounded-xl border p-4" style={{ borderColor: t.border }}><strong>{task.customer_name} · {task.product_name}</strong><p className="mt-1 text-sm" style={{ color: t.textTertiary }}>{task.customer_phone || "No phone recorded"} · {task.branch || "No branch"}</p><p className="mt-2 text-xs" style={{ color: t.textTertiary }}>{task.task_no} · {statusLabel(task.status)} · Due {task.due_date}<br />Triggered by {task.triggers.map(trigger => trigger === "card_closed" ? "payment card closed" : "marked delivered").join(" / ")}</p></div>
    <div className="space-y-4"><label className="block text-sm font-semibold">Contact outcome<select value={String(form.contacted)} onChange={e => setForm({ ...form, contacted: e.target.value === "true" })} className={input} style={card}><option value="true">Customer reached</option><option value="false">Could not reach customer</option></select></label>
    {form.contacted && <><div className="grid gap-4 sm:grid-cols-2"><label className="block text-sm font-semibold">Item received *<select required value={form.receipt} onChange={e => setForm({ ...form, receipt: e.target.value })} className={input} style={card}><option value="">Confirm receipt</option><option value="full">Fully received</option><option value="partial">Partly received</option><option value="none">Not received</option></select></label><label className="block text-sm font-semibold">Customer satisfaction *<select required value={form.satisfaction} onChange={e => setForm({ ...form, satisfaction: e.target.value })} className={input} style={card}><option value="">Select outcome</option><option value="satisfied">Satisfied</option><option value="issue">Customer reports an issue</option></select></label></div><fieldset><legend className="text-sm font-semibold">Customer rating *</legend><div className="mt-2 flex items-center gap-2" role="radiogroup" aria-label="Customer rating">{[1,2,3,4,5].map(star => <button type="button" role="radio" aria-checked={form.rating === star} aria-label={`${star} star${star === 1 ? "" : "s"}`} key={star} onClick={() => setForm({ ...form, rating: star })} className="rounded-lg p-1.5 focus:ring-2 focus:ring-amber-400"><Star size={29} className={star <= form.rating ? "fill-amber-400 text-amber-400" : "text-gray-300"} /></button>)}<span className="text-sm">{form.rating ? `${form.rating}/5` : "Select 1–5"}</span></div></fieldset><label className="block text-sm font-semibold">Outstanding issue{form.receipt && (form.receipt !== "full" || form.satisfaction === "issue") ? " *" : ""}<textarea required={Boolean(form.receipt && (form.receipt !== "full" || form.satisfaction === "issue"))} rows={3} maxLength={2000} value={form.issue} onChange={e => setForm({ ...form, issue: e.target.value })} className={input} style={card} placeholder="Missing items, damage, delivery concern, or other issue. Clear after resolution." /></label></>}
    <label className="block text-sm font-semibold">Follow-up notes *<textarea required rows={3} maxLength={2000} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} className={input} style={card} placeholder="Record what the customer said and the action taken." /></label>
    {!complete && <label className="block text-sm font-semibold">Next follow-up date *<input required type="date" min={new Date().toISOString().slice(0,10)} value={form.next_followup_date} onChange={e => setForm({ ...form, next_followup_date: e.target.value })} className={input} style={card} /></label>}
    {complete && !task.repeat_purchase && <RepeatPurchaseFields value={repeat} onChange={setRepeat} agent={repeatAgent} onAgentChange={setRepeatAgent} />}
    {task.repeat_purchase && <div className="rounded-xl border p-4 text-sm" style={{ borderColor: t.border }}><strong>Repeat purchase: {task.repeat_purchase.requested_product}</strong><p className="mt-1">{task.repeat_purchase.agent_name} · {task.repeat_purchase.status}</p><a href="#repeatpurchases" className="mt-2 inline-block font-semibold underline" onClick={onClose}>Track opportunity</a></div>}
    <p className="rounded-lg p-3 text-sm" style={{ background: complete ? t.successBg : t.warnBg, color: complete ? t.successText : t.warnText }}>{complete ? "Saving will confirm full receipt and complete the follow-up task." : "The task stays open until full receipt is confirmed and all outstanding issues are resolved."}</p>
    {error && <p role="alert" className="text-sm" style={{ color: t.dangerText }}>{error}</p>}
    <div className="flex justify-end gap-3"><button type="button" disabled={saving} onClick={onClose} className="rounded-lg border px-4 py-2" style={card}>Cancel</button><button disabled={saving} className="rounded-lg px-4 py-2 font-bold text-white disabled:opacity-50" style={{ background: t.accent }}>{saving ? "Saving…" : complete ? "Confirm & complete" : "Save follow-up"}</button></div></div>
    {!!task.history.length && <section className="mt-6 border-t pt-4" style={{ borderColor: t.border }}><h3 className="mb-3 font-bold">Review history</h3><div className="space-y-3">{[...task.history].reverse().map((review,index) => <article key={index} className="rounded-xl border p-3 text-sm" style={{ borderColor: t.border }}><div className="flex flex-wrap justify-between gap-2"><strong>{review.officer_name}</strong><RecordAttribution record={review}/><RatingStars value={review.rating} /></div><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>{new Date(review.recorded_at).toLocaleString("en-GB", { timeZone: "Africa/Accra" })} · {review.contacted ? receiptLabel[review.receipt] : "Customer not reached"}</p>{review.issue && <p className="mt-2" style={{ color: t.dangerText }}>Issue: {review.issue}</p>}<p className="mt-2 whitespace-pre-wrap">{review.notes}</p></article>)}</div></section>}
    </>}</form></div>;
}

export function DeliveryFollowupQueue({ onReviewed }: { onReviewed: () => void }) {
  const { t } = useTheme();
  const [rows, setRows] = useState<Followup[]>([]);
  const [count, setCount] = useState(0);
  const [state, setState] = useState("open");
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState({ page: 1, total: 0, total_pages: 1 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [taskId, setTaskId] = useState("");
  const load = async () => {
    setLoading(true); setError("");
    try { const response = await fetch(`/api/customer-support/delivery-followups?status=${state}&page=${page}&per_page=25`, { credentials: "same-origin" }); const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.message || "Unable to load follow-ups."); setRows(data.followups); setCount(data.open_count); setPagination(data.pagination); setPage(data.pagination.page); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load follow-ups."); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); const refresh = () => void load(); window.addEventListener("customer-support-delivery-reviews-changed", refresh); return () => window.removeEventListener("customer-support-delivery-reviews-changed", refresh); }, [state, page]);
  const card = { background: t.surface, color: t.textPrimary, borderColor: t.border };
  return <section className="overflow-hidden rounded-xl border" style={card}><header className="flex flex-wrap items-center justify-between gap-3 border-b p-5" style={{ borderColor: t.border }}><div><h2 className="flex items-center gap-2 font-bold"><CheckCircle2 size={19} />Delivery confirmation <span className="rounded-full px-2 py-1 text-xs" style={{ background: t.warnBg, color: t.warnText }}>{count} open</span></h2><p className="mt-1 text-xs" style={{ color: t.textTertiary }}>Automatic follow-ups for delivered items and closed payment cards, including cards without a submitted delivery.</p></div><div className="flex gap-2"><select aria-label="Follow-up status" value={state} onChange={e => { setState(e.target.value); setPage(1); }} className="rounded-lg border px-3 py-2 text-sm" style={card}><option value="open">Open follow-ups</option><option value="completed">Confirmed</option><option value="all">All follow-ups</option></select><button disabled={loading} aria-label="Refresh follow-ups" onClick={() => void load()} className="rounded-lg border p-2" style={card}><RefreshCw size={16} /></button></div></header>
    {error && <p role="alert" className="p-4 text-sm" style={{ color: t.dangerText }}>{error}</p>}
    <div className="max-h-[420px] overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Customer / item", "Trigger", "Due", "Receipt", "Satisfaction", ""].map(label => <th key={label} className="px-4 py-3">{label}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.id} className="border-t" style={{ borderColor: t.border }}><td className="px-4 py-3"><strong>{row.customer_name}</strong><span className="block text-xs" style={{ color: t.textTertiary }}>{row.product_name} · {row.customer_phone || "No phone"}</span></td><td className="px-4 py-3 text-xs">{row.triggers.map(trigger => trigger === "card_closed" ? "Card closed" : "Delivered").join(" / ")}</td><td className="px-4 py-3">{row.due_date}</td><td className="px-4 py-3">{receiptLabel[row.review?.receipt || "unknown"]}</td><td className="px-4 py-3"><RatingStars value={row.review?.rating || null} /><span className="block text-xs" style={{ color: row.status === "completed" ? t.successText : t.warnText }}>{statusLabel(row.status)}</span>{row.review?.issue && <p className="mt-1 max-w-xs text-xs" style={{ color: t.dangerText }}>{row.review.issue}</p>}</td><td className="px-4 py-3"><button onClick={() => setTaskId(row.id)} className="flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-xs font-bold text-white" style={{ background: t.accent }}><PhoneCall size={14} />{row.status === "completed" ? "View / review" : "Follow up"}</button></td></tr>)}{!rows.length && <tr><td colSpan={6} className="p-8 text-center" style={{ color: t.textTertiary }}>{loading ? "Loading follow-ups…" : "No follow-ups in this view."}</td></tr>}</tbody></table></div>
    <footer className="flex items-center justify-between gap-3 border-t px-4 py-3 text-xs" style={{ borderColor: t.border, color: t.textTertiary }}><span>{pagination.total} follow-ups ? Page {pagination.page} of {pagination.total_pages}</span><div className="flex gap-2"><button disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)} className="rounded-lg border px-3 py-2 disabled:opacity-40" style={card}>Previous</button><button disabled={loading || page >= pagination.total_pages} onClick={() => setPage(value => value + 1)} className="rounded-lg border px-3 py-2 disabled:opacity-40" style={card}>Next</button></div></footer>
    {taskId && <DeliveryFollowupModal taskId={taskId} onClose={() => setTaskId("")} onSaved={onReviewed} />}
  </section>;
}
