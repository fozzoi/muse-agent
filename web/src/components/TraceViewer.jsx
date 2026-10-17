import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, Loader2, X } from "lucide-react";

const shortModel = (m) => (m ? m.split("/").pop() : null);

function StepIcon({ step }) {
  if (step.retry) return <AlertTriangle className="size-3.5 text-rose-400" />;
  if (step.state === "active") return <span className="block size-2.5 rounded-full bg-cyan-400 animate-pulse-ring" />;
  if (step.state === "failed") return <X className="size-3.5 text-rose-400" />;
  return <Check className="size-3.5 text-emerald-400" />;
}

export default function TraceViewer({ steps, streaming }) {
  const [userOpen, setUserOpen] = useState(null); // null = auto (open while streaming)
  const open = userOpen ?? streaming;
  if (!steps.length) return null;

  const current = steps[steps.length - 1];
  const retries = steps.filter((s) => s.retry).length;

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] text-xs">
      <button onClick={() => setUserOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        {streaming ? <Loader2 className="size-3.5 animate-spin text-cyan-400" /> : <Check className="size-3.5 text-emerald-400" />}
        <span className="truncate text-zinc-300">{streaming ? current.label : `${steps.length} steps`}</span>
        {retries > 0 && (
          <span className="rounded-full bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-300">
            {retries} {retries === 1 ? "retry" : "retries"}
          </span>
        )}
        <ChevronDown className={`ml-auto size-3.5 text-zinc-500 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <ol className="relative mx-3 mb-3 space-y-2.5 border-l border-white/10 pl-4 animate-fade-in">
          {steps.map((s) => (
            <li key={s.id} className="relative">
              <span className="absolute -left-[1.55rem] top-0.5 flex size-4 items-center justify-center rounded-full bg-zinc-950">
                <StepIcon step={s} />
              </span>
              <div className={s.state === "active" ? "text-zinc-100" : "text-zinc-400"}>{s.label}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {s.model && <span className="rounded bg-indigo-400/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">{shortModel(s.model)}</span>}
                {s.tools != null && <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-zinc-400">{s.tools} tools</span>}
                {s.decision?.tool_name && <span className="rounded bg-cyan-400/10 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">{s.decision.tool_name}</span>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}