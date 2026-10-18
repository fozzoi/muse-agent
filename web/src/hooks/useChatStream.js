import { useCallback, useEffect, useRef, useState } from "react";
import { getChatHistory, streamEndpoint } from "../lib/api";

const RETRY_RE = /retry|attempt|self-debug|fixing|failed|error/i;
let n = 0;
const nid = () => `${Date.now()}-${n++}`;

const settle = (steps, to) => steps.map((s) => (s.state === "active" ? { ...s, state: to } : s));

export function useChatStream({ onToolCreated } = {}) {
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const abortCtrlRef = useRef(null);
  const idRef = useRef(null);
  const cbRef = useRef(onToolCreated);
  cbRef.current = onToolCreated;

  const patch = useCallback(
    (id, fn) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m))),
    []
  );

  const close = useCallback(() => {
    if (abortCtrlRef.current) {
      abortCtrlRef.current.abort();
      abortCtrlRef.current = null;
    }
    setStreaming(false);
  }, []);

  const send = useCallback(
    async (prompt, imageAttachment = null) => {
      const text = prompt.trim();
      if ((!text && !imageAttachment) || abortCtrlRef.current) return;

      const id = nid();
      idRef.current = id;
      setMessages((ms) => [
        ...ms,
        {
          id: nid(),
          role: "user",
          text: text || "Analyze this image",
          image: imageAttachment?.previewUrl || null,
        },
        { id, role: "assistant", text: "", steps: [], status: "streaming" },
      ]);
      setStreaming(true);

      const controller = new AbortController();
      abortCtrlRef.current = controller;
      let finished = false;

      const finish = (extra, stepState = "done") => {
        if (finished) return;
        finished = true;
        patch(id, (m) => ({ ...m, ...extra, steps: settle(m.steps, stepState) }));
        close();
      };

      try {
        const resp = await fetch(streamEndpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt: text,
            image: imageAttachment?.data || null,
            mime_type: imageAttachment?.mimeType || "image/jpeg",
          }),
          signal: controller.signal,
        });

        if (!resp.ok) {
          throw new Error(`Server returned ${resp.status}: ${resp.statusText}`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split(/\r?\n\r?\n/);
          buffer = blocks.pop() || "";

          for (const block of blocks) {
            if (!block.trim()) continue;
            let eventName = "message";
            const dataLines = [];

            for (const line of block.split(/\r?\n/)) {
              if (line.startsWith("event:")) {
                eventName = line.slice(6).trim();
              } else if (line.startsWith("data:")) {
                dataLines.push(line.slice(5).trim());
              }
            }

            const eventData = dataLines.join("\n");

            if (eventName === "status" && eventData) {
              let d;
              try { d = JSON.parse(eventData); } catch { continue; }
              const label = d.step || "Working…";
              patch(id, (m) => ({
                ...m,
                steps: [
                  ...settle(m.steps, "done"),
                  {
                    id: nid(),
                    label,
                    state: "active",
                    retry: RETRY_RE.test(label),
                    model: d.router_model,
                    decision: d.decision,
                    tools: d.tools_count,
                  },
                ],
              }));
            } else if (eventName === "result" && eventData) {
              let d;
              try { d = JSON.parse(eventData); } catch {
                return finish({ status: "error", error: "Malformed response from backend" }, "failed");
              }
              finish({
                status: "done",
                text: d.text ?? "",
                raw: d.raw_tool_output,
                model: d.model,
                toolCreated: d.tool_created,
              });
              if (d.tool_created) cbRef.current?.(d.tool_created);
            } else if (eventName === "error" && eventData) {
              let msg = "Unknown error";
              try { msg = JSON.parse(eventData).error || msg; } catch { msg = eventData; }
              finish({ status: "error", error: msg }, "failed");
            }
          }
        }

        if (!finished && buffer.trim()) {
          const block = buffer.trim();
          let eventName = "message";
          const dataLines = [];
          for (const line of block.split(/\r?\n/)) {
            if (line.startsWith("event:")) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              dataLines.push(line.slice(5).trim());
            }
          }
          const eventData = dataLines.join("\n");
          if (eventName === "result" && eventData) {
            try {
              const d = JSON.parse(eventData);
              finish({
                status: "done",
                text: d.text ?? "",
                raw: d.raw_tool_output,
                model: d.model,
                toolCreated: d.tool_created,
              });
              if (d.tool_created) cbRef.current?.(d.tool_created);
            } catch {}
          }
        }

        if (!finished) {
          finish({ status: "done" });
        }
      } catch (err) {
        if (finished) return;
        if (err.name === "AbortError") {
          patch(id, (m) => ({ ...m, status: "aborted", steps: settle(m.steps, "failed") }));
        } else {
          finish({ status: "error", error: err.message || "Lost connection to backend." }, "failed");
        }
        close();
      }
    },
    [close, patch]
  );

  const abort = useCallback(() => {
    if (abortCtrlRef.current) {
      abortCtrlRef.current.abort();
    }
  }, []);

  const reset = useCallback(() => {
    close();
    setMessages([]);
  }, [close]);

  useEffect(() => {
    let active = true;
    getChatHistory()
      .then((history) => {
        if (!active || !Array.isArray(history) || history.length === 0) return;
        setMessages(
          history.map((msg, idx) => ({
            id: `hist-${idx}-${Date.now()}`,
            role: msg.role,
            text: msg.content,
            image: msg.image || null,
            status: "done",
            steps: [],
          }))
        );
      })
      .catch(() => {});

    return () => {
      active = false;
      abortCtrlRef.current?.abort();
    };
  }, []);

  return { messages, streaming, send, abort, reset };
}