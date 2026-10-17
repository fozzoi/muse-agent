import { useState } from "react";
import { Braces, Check, Eraser, Loader2, RefreshCw, Trash2, X } from "lucide-react";

function ToolCard({ name, description, onInspect, onDelete }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  const doDelete = async () => {
    setBusy(true);
    try { await onDelete(name); } finally { setBusy(false); setConfirm(false); }
  };

  return (
    <li className="rounded-xl border border-white/10 bg-white/[0.03] p-3 hover:border-white/20 transition">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[13px] font-medium truncate">{name}</span>
        <span className="ml-auto shrink-0 rounded-md bg-cyan-400/10 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">.py</span>
      </div>
      <p className="mt-1 text-xs leading-snug text-zinc-400 line-clamp-2">{description}</p>
      <div className="mt-2.5 flex items-center gap-1.5">
        <button onClick={() => onInspect({ name, description })}
          className="flex items-center gap-1.5 rounded-md bg-white/5 px-2 py-1 text-xs hover:bg-white/10 transition">
          <Braces className="size-3.5" /> Inspect code
        </button>
        {confirm ? (
          <div className="ml-auto flex items-center gap-1 animate-fade-in">
            <span className="text-xs text-zinc-400 mr-1">Delete?</span>
            <button onClick={doDelete} disabled={busy} aria-label="Confirm delete"
              className="p-1.5 rounded-md bg-rose-500/20 text-rose-300 hover:bg-rose-500/30">
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            </button>
            <button onClick={() => setConfirm(false)} aria-label="Cancel" className="p-1.5 rounded-md hover:bg-white/10">
              <X className="size-3.5" />
            </button>
          </div>
        ) : (
          <button onClick={() => setConfirm(true)} aria-label={`Delete ${name}`}
            className="ml-auto p-1.5 rounded-md text-zinc-500 hover:text-rose-300 hover:bg-rose-500/10 transition">
            <Trash2 className="size-3.5" />
          </button>
        )}
      </div>
    </li>
  );
}

export default function Sidebar({ tools, loading, error, open, collapsed, onClose, onRefresh, onClear, onInspect, onDelete }) {
  const entries = Object.entries(tools);
  return (
    <>
      <div onClick={onClose}
        className={`fixed inset-0 z-30 bg-black/60 backdrop-blur-sm md:hidden transition-opacity ${open ? "opacity-100" : "pointer-events-none opacity-0"}`} />
      <aside
        className={`glass fixed inset-y-0 left-0 z-40 flex w-[19rem] shrink-0 flex-col border-y-0 border-l-0 bg-zinc-950/90 transition-transform duration-200
          md:static md:z-auto md:bg-white/[0.02] md:transition-[margin]
          ${open ? "translate-x-0" : "-translate-x-full"} ${collapsed ? "md:-ml-[19rem]" : "md:translate-x-0 md:ml-0"}`}>
        <div className="flex items-center gap-2 p-4 border-b border-white/10">
          <h2 className="text-sm font-medium">Tools</h2>
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[11px] text-zinc-300">{entries.length}</span>
          <button onClick={onRefresh} aria-label="Refresh tools" className="ml-auto p-1.5 rounded-md hover:bg-white/10 transition">
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button onClick={onClose} aria-label="Close sidebar" className="p-1.5 rounded-md hover:bg-white/10 md:hidden">
            <X className="size-4" />
          </button>
        </div>

        <ul className="flex-1 space-y-2 overflow-y-auto p-3">
          {error && <li className="rounded-lg bg-rose-500/10 p-3 text-xs text-rose-300">Couldn't reach the backend: {error}</li>}
          {!error && !loading && entries.length === 0 && (
            <li className="p-3 text-xs text-zinc-500">No tools yet. Ask for something the agent can't do and it'll write one.</li>
          )}
          {entries.map(([name, t]) => (
            <ToolCard key={name} name={name} description={t.description} onInspect={onInspect} onDelete={onDelete} />
          ))}
        </ul>

        <div className="p-3 border-t border-white/10">
          <button onClick={onClear}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-white/5 py-2 text-xs hover:bg-white/10 transition">
            <Eraser className="size-3.5" /> Clear chat memory
          </button>
        </div>
      </aside>
    </>
  );
}