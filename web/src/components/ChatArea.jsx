import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUp, ChevronRight, ImageIcon, Sparkles, Square, TerminalSquare, TriangleAlert, Wrench, X } from "lucide-react";
import TraceViewer from "./TraceViewer";
import { API } from "../lib/api";

const SUGGESTIONS = ["Check root disk space", "Print system specs", "Fetch current memory usage"];

const md = {
  p: (p) => <p className="leading-relaxed [&:not(:first-child)]:mt-3" {...p} />,
  ul: (p) => <ul className="mt-3 list-disc space-y-1 pl-5" {...p} />,
  ol: (p) => <ol className="mt-3 list-decimal space-y-1 pl-5" {...p} />,
  a: (p) => <a className="text-cyan-300 underline underline-offset-2" target="_blank" rel="noreferrer" {...p} />,
  strong: (p) => <strong className="font-semibold text-zinc-50" {...p} />,
  code: ({ className, children, ...p }) =>
    className || String(children).includes("\n") ? (
      <code className="block overflow-x-auto rounded-lg bg-black/40 p-3 font-mono text-[13px]" {...p}>{children}</code>
    ) : (
      <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[0.85em]" {...p}>{children}</code>
    ),
  pre: (p) => <pre className="mt-3" {...p} />,
  table: (p) => <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm" {...p} /></div>,
  th: (p) => <th className="border-b border-white/10 px-2 py-1 font-medium" {...p} />,
  td: (p) => <td className="border-b border-white/5 px-2 py-1" {...p} />,
};

function prettyRaw(raw) {
  try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return String(raw); }
}

function ModelPill({ model }) {
  const [provider, name] = model.includes("/") ? model.split("/") : [null, model];
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-indigo-400/20 bg-indigo-400/10 px-2 py-0.5 font-mono text-[11px] text-indigo-200">
      <Sparkles className="size-3" />
      {provider && <span className="text-indigo-300/60">{provider}/</span>}{name}
    </span>
  );
}

function RawDrawer({ raw }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 transition">
        <ChevronRight className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
        <TerminalSquare className="size-3.5" /> Raw tool output
      </button>
      {open && (
        <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-white/10 bg-black/40 p-3 font-mono text-xs text-emerald-200/90 animate-fade-in">
          {prettyRaw(raw)}
        </pre>
      )}
    </div>
  );
}

function cleanMessageText(text) {
  if (typeof text === "string" && text.startsWith("[Image attached] ")) {
    return text.slice(17);
  }
  return text;
}

function Message({ m }) {
  if (m.role === "user") {
    const imgSrc = m.image
      ? m.image.startsWith("http") || m.image.startsWith("data:")
        ? m.image
        : `${API}${m.image}`
      : null;

    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-indigo-500/20 px-4 py-2.5 text-[15px] text-zinc-100 ring-1 ring-inset ring-indigo-400/20">
          {imgSrc && (
            <img
              src={imgSrc}
              alt="Uploaded thumbnail"
              className="mb-2 max-h-60 rounded-xl object-contain border border-white/10"
            />
          )}
          {cleanMessageText(m.text)}
        </div>
      </div>
    );
  }
  const streaming = m.status === "streaming";
  return (
    <div className="space-y-3 animate-fade-in">
      <TraceViewer steps={m.steps} streaming={streaming} />
      {m.status === "error" && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 p-3 text-sm text-rose-200">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" /> <span className="break-words">{m.error}</span>
        </div>
      )}
      {m.status === "aborted" && <p className="text-xs text-zinc-500">Stopped.</p>}
      {m.status === "done" && (
        <div className="text-[15px] text-zinc-200">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={md}>{m.text}</ReactMarkdown>
          {m.toolCreated && (
            <div className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-emerald-400/10 px-2.5 py-1 text-xs text-emerald-300">
              <Wrench className="size-3" /> New tool: <span className="font-mono">{m.toolCreated}</span>
            </div>
          )}
          {m.raw && <RawDrawer raw={m.raw} />}
          {m.model && <div className="mt-3"><ModelPill model={m.model} /></div>}
        </div>
      )}
    </div>
  );
}

function Composer({ streaming, onSend, onAbort }) {
  const [value, setValue] = useState("");
  const [attachment, setAttachment] = useState(null);
  const fileInputRef = useRef(null);
  const ref = useRef(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 200) + "px";
    }
  }, [value]);

  const handleFile = (file) => {
    if (!file || !file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target.result;
      const base64Data = dataUrl.split(",")[1];
      setAttachment({
        data: base64Data,
        mimeType: file.type,
        previewUrl: dataUrl,
      });
    };
    reader.readAsDataURL(file);
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) handleFile(file);
      }
    }
  };

  const submit = () => {
    if ((!value.trim() && !attachment) || streaming) return;
    onSend(value, attachment);
    setValue("");
    setAttachment(null);
  };

  return (
    <div
      className="glass flex flex-col rounded-2xl p-2 focus-within:border-cyan-400/40 transition-colors"
      onPaste={handlePaste}
    >
      {attachment && (
        <div className="relative mb-2 ml-2 inline-flex items-center gap-2 self-start rounded-xl border border-white/10 bg-black/40 p-1.5 animate-fade-in">
          <img src={attachment.previewUrl} alt="Preview" className="h-16 w-16 rounded-lg object-cover" />
          <button
            type="button"
            onClick={() => setAttachment(null)}
            aria-label="Remove image"
            className="absolute -right-2 -top-2 flex size-5 items-center justify-center rounded-full bg-zinc-800 text-zinc-300 hover:bg-rose-500 hover:text-white transition"
          >
            <X className="size-3" />
          </button>
        </div>
      )}
      <div className="flex items-end gap-2">
        <input
          type="file"
          ref={fileInputRef}
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.[0]) handleFile(e.target.files[0]);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          aria-label="Attach image"
          title="Upload or paste an image"
          className="grid size-9 shrink-0 place-items-center rounded-xl text-zinc-400 hover:bg-white/10 hover:text-zinc-200 transition"
        >
          <ImageIcon className="size-4" />
        </button>
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder={attachment ? "Ask a question about this image…" : "Ask Muse to do something, or attach an image…"}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-zinc-500"
        />
        {streaming ? (
          <button
            type="button"
            onClick={onAbort}
            aria-label="Stop"
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 transition"
          >
            <Square className="size-4 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() && !attachment}
            aria-label="Send"
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-cyan-400 text-zinc-950 transition hover:bg-cyan-300 disabled:bg-white/10 disabled:text-zinc-500"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}

export default function ChatArea({ messages, streaming, onSend, onAbort }) {
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-6 px-4 py-6">
          {messages.length === 0 ? (
            <div className="m-auto text-center">
              <h2 className="text-2xl font-semibold tracking-tight">What should Muse do?</h2>
              <p className="mt-2 text-sm text-zinc-400">Attach an image to analyze, or ask for a task and it will write tools for it.</p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => onSend(s)}
                    className="rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-1.5 text-sm text-zinc-300 transition hover:border-cyan-400/40 hover:text-white">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => <Message key={m.id} m={m} />)
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div className="mx-auto w-full max-w-3xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <Composer streaming={streaming} onSend={onSend} onAbort={onAbort} />
        <p className="mt-2 text-center text-[11px] text-zinc-600">Enter to send · Shift+Enter for new line · Paste or click icon to attach image</p>
      </div>
    </div>
  );
}