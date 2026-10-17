import { useCallback, useEffect, useRef, useState } from "react";
import { streamUrl } from "../lib/api";

const RETRY_RE = /retry|attempt|self-debug|fixing|failed|error/i;
let n = 0;
const nid = () => `${Date.now()}-${n++}`;

const settle = (steps, to) => steps.map((s) => (s.state === "active" ? { ...s, state: to } : s));

export function useChatStream({ onToolCreated } = {}) {
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const esRef = useRef(null);
  const idRef = useRef(null);
  const cbRef = useRef(onToolCreated);
  cbRef.current = onToolCreated;

  const patch = useCallback(
    (id, fn) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m))),
    []
  );

  const close = useCallback(() => {
    esRef.current?.close(); // closing stops EventSource's auto-reconnect (which would re-send the prompt)
    esRef.current = null;
    setStreaming(false);
  }, []);

  const send = useCallback(
    (prompt) => {
      const text = prompt.trim();
      if (!text || esRef.current) return;

      const id = nid();
      idRef.current = id;
      setMessages((ms) => [
        ...ms,
        { id: nid(), role: "user", text },
        { id, role: "assistant", text: "", steps: [], status: "streaming" },
      ]);
      setStreaming(true);

      const es = new EventSource(streamUrl(text));
      esRef.current = es;
      let finished = false;

      const finish = (extra, stepState = "done") => {
        if (finished) return;
        finished = true;
        patch(id, (m) => ({ ...m, ...extra, steps: settle(m.steps, stepState) }));
        close();
      };

      es.addEventListener("status", (e) => {
        let d;
        try { d = JSON.parse(e.data); } catch { return; }
        const label = d.step || "Working…";
        patch(id, (m) => ({
          ...m,
          steps: [
            ...settle(m.steps, "done"),
            {
              id: nid(), label, state: "active", retry: RETRY_RE.test(label),
              model: d.router_model, decision: d.decision, tools: d.tools_count,
            },
          ],
        }));
      });

      es.addEventListener("result", (e) => {
        let d;
        try { d = JSON.parse(e.data); } catch {
          return finish({ status: "error", error: "Malformed response from backend" }, "failed");
        }
        finish({ status: "done", text: d.text ?? "", raw: d.raw_tool_output, model: d.model, toolCreated: d.tool_created });
        if (d.tool_created) cbRef.current?.(d.tool_created);
      });

      // Handles BOTH server-sent `event: error` (has e.data) and native connection errors (no e.data).
      es.addEventListener("error", (e) => {
        if (e.data) {
          let msg = "Unknown error";
          try { msg = JSON.parse(e.data).error || msg; } catch { msg = e.data; }
          finish({ status: "error", error: msg }, "failed");
        } else {
          finish({ status: "error", error: "Lost connection to the backend. Is it running?" }, "failed");
        }
      });
    },
    [close, patch]
  );

  const abort = useCallback(() => {
    if (!esRef.current) return;
    patch(idRef.current, (m) => ({ ...m, status: "aborted", steps: settle(m.steps, "failed") }));
    close();
  }, [close, patch]);

  const reset = useCallback(() => { close(); setMessages([]); }, [close]);

  useEffect(() => () => esRef.current?.close(), []);

  return { messages, streaming, send, abort, reset };
}