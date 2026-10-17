import { useEffect, useState } from "react";
import { Check, Copy, FileCode2, Loader2, X } from "lucide-react";
import { getToolCode } from "../lib/api";

const KW = "def|class|import|from|return|if|elif|else|for|while|in|not|and|or|try|except|finally|with|as|lambda|None|True|False|raise|pass|yield|print";
const RE = new RegExp(
  `(#.*$)|("""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*')|\\b(${KW})\\b|\\b(\\d+(?:\\.\\d+)?)\\b`,
  "gm"
);
const COLORS = ["", "text-zinc-500 italic", "text-emerald-300", "text-indigo-300", "text-amber-300"];

function highlight(code) {
  const out = [];
  let last = 0;
  for (const m of code.matchAll(RE)) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const g = m.findIndex((v, i) => i > 0 && v !== undefined);
    out.push(<span key={m.index} className={COLORS[g]}>{m[0]}</span>);
    last = m.index + m[0].length;
  }
  out.push(code.slice(last));
  return out;
}

export default function CodeModal({ tool, onClose }) {
  const [code, setCode] = useState(null);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    getToolCode(tool.name).then((d) => live && setCode(d.code)).catch((e) => live && setErr(e.message));
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { live = false; window.removeEventListener("keydown", onKey); };
  }, [tool.name, onClose]);

  const copy = async () => {
    await navigator.clipboard.writeText(code ?? "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-6" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full sm:max-w-3xl max-h-[88vh] flex flex-col rounded-t-2xl sm:rounded-2xl border border-white/10 bg-zinc-900/95 shadow-2xl animate-pop-in">
        <header className="flex items-start gap-3 p-4 border-b border-white/10">
          <FileCode2 className="size-5 mt-0.5 text-cyan-400 shrink-0" />
          <div className="min-w-0 flex-1">
            <h2 className="font-mono text-sm font-medium truncate">{tool.name}.py</h2>
            <p className="text-xs text-zinc-400 mt-0.5">{tool.description}</p>
          </div>
          <button onClick={copy} disabled={!code}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs bg-white/5 hover:bg-white/10 disabled:opacity-40 transition">
            {copied ? <Check className="size-3.5 text-emerald-400" /> : <Copy className="size-3.5" />}
            {copied ? "Copied" : "Copy code"}
          </button>
          <button onClick={onClose} aria-label="Close" className="p-1.5 rounded-lg hover:bg-white/10 transition">
            <X className="size-4" />
          </button>
        </header>
        <div className="overflow-auto p-4">
          {err ? (
            <p className="text-sm text-rose-400">Couldn't load the code: {err}</p>
          ) : code === null ? (
            <div className="flex items-center gap-2 text-sm text-zinc-400"><Loader2 className="size-4 animate-spin" />Loading…</div>
          ) : (
            <pre className="font-mono text-[13px] leading-relaxed text-zinc-300 whitespace-pre">{highlight(code)}</pre>
          )}
        </div>
      </div>
    </div>
  );
}