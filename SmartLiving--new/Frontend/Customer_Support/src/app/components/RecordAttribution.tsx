export type SupportActor = { user_id: string; name: string; role: string; branch: string; at: string };
export type SupportAttribution = { support_recorded_by?: SupportActor; support_last_updated_by?: SupportActor };

export function RecordAttribution({ record }: { record: SupportAttribution }) {
  const author = record.support_recorded_by, editor = record.support_last_updated_by;
  const label = (actor: SupportActor) => actor.role === "manager" ? "Manager" : actor.role === "customer_support" ? "Main Customer Support" : actor.role;
  const badge = (actor: SupportActor, verb: string) => <span
    title={`${actor.branch || ""}${actor.at ? ` · ${new Date(actor.at).toLocaleString("en-GB", {timeZone: "Africa/Accra"})}` : ""}`}
    style={{display: "inline-block", borderRadius: 8, padding: "3px 7px", fontSize: 10, fontWeight: 600, whiteSpace: "normal",
      background: actor.role === "manager" ? "#cffafe" : "#ede9fe", color: actor.role === "manager" ? "#155e75" : "#5b21b6"}}>
    {verb} by {label(actor)} · {actor.name}
  </span>;
  return <div className="flex flex-wrap gap-1 mt-1">
    {author && badge(author, "Recorded")}
    {editor?.role === "manager" && (editor.user_id !== author?.user_id || editor.at !== author?.at) && badge(editor, "Updated")}
  </div>;
}
