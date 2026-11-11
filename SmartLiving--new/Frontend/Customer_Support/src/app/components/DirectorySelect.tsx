import { useEffect, useState } from "react";
import { Search, X } from "lucide-react";

export type DirectoryOption = { id: string; name: string; phone?: string; location?: string; branch?: string; imageUrl?: string };

export function DirectorySelect({ kind, value, onChange, placeholder }: { kind: "customers" | "agents"; value: DirectoryOption | null; onChange: (value: DirectoryOption | null) => void; placeholder: string }) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<DirectoryOption[]>([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`/api/customer-support/directory/${kind}?q=${encodeURIComponent(query)}`, { signal: controller.signal, credentials: "same-origin" });
        const data = await response.json();
        setOptions(kind === "customers" ? data.customers ?? [] : data.agents ?? []);
      } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) setOptions([]); }
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [kind, open, query]);
  const avatar = (option: DirectoryOption) => option.imageUrl ? <img src={option.imageUrl} className="h-9 w-9 rounded-full object-cover" /> : <div className="flex h-9 w-9 items-center justify-center rounded-full bg-gray-100 text-xs font-bold text-gray-600">{option.name.split(" ").map(p => p[0]).slice(0,2).join("")}</div>;
  const meta = (option: DirectoryOption) => kind === "customers" ? [option.phone, option.location].filter(Boolean).join(" | ") : option.branch || "Unassigned branch";
  return <div className="relative">
    {value ? <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-2.5">{avatar(value)}<div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold text-gray-900">{value.name}</div><div className="truncate text-xs text-gray-500">{meta(value)}</div></div><button type="button" aria-label="Clear selection" onClick={() => { onChange(null); setQuery(""); setOpen(true); }}><X size={16} /></button></div>
      : <div className="relative"><Search size={15} className="absolute left-3 top-3 text-gray-400" /><input value={query} onChange={e => { setQuery(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} placeholder={placeholder} className="w-full rounded-lg border border-gray-200 bg-gray-50 py-2.5 pl-9 pr-3 text-sm outline-none focus:border-blue-600" /></div>}
    {open && !value && <div className="absolute z-[70] mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-xl">{options.map(option => <button type="button" key={option.id} onMouseDown={e => e.preventDefault()} onClick={() => { onChange(option); setOpen(false); }} className="flex w-full items-center gap-3 border-b border-gray-100 p-3 text-left hover:bg-blue-50">{avatar(option)}<div className="min-w-0"><div className="truncate text-sm font-semibold text-gray-900">{option.name}</div><div className="truncate text-xs text-gray-500">{meta(option)}</div></div></button>)}{!options.length && <div className="p-4 text-center text-sm text-gray-500">No matching {kind}</div>}</div>}
  </div>;
}
