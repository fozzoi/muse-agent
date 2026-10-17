import { useCallback, useEffect, useState } from "react";
import { Menu, PanelLeft } from "lucide-react";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import CodeModal from "./components/CodeModal";
import { useChatStream } from "./hooks/useChatStream";
import * as api from "./lib/api";
import "./index.css"

export default function App() {
  const [tools, setTools] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [drawer, setDrawer] = useState(false);      // mobile overlay
  const [collapsed, setCollapsed] = useState(false); // desktop
  const [inspecting, setInspecting] = useState(null);

  const loadTools = useCallback(async () => {
    setLoading(true);
    try { setTools(await api.getTools()); setError(null); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadTools(); }, [loadTools]);

  const { messages, streaming, send, abort, reset } = useChatStream({ onToolCreated: loadTools });

  const clearMemory = async () => {
    try { await api.clearMemory(); } catch (e) { setError(e.message); return; }
    reset();
  };

  const deleteTool = async (name) => {
    try { await api.deleteTool(name); await loadTools(); } catch (e) { setError(e.message); }
  };

  const toggleSidebar = () => (window.matchMedia("(min-width: 768px)").matches ? setCollapsed((c) => !c) : setDrawer((d) => !d));

  return (
    <div className="flex h-full overflow-hidden">
      <Sidebar
        tools={tools} loading={loading} error={error} open={drawer} collapsed={collapsed}
        onClose={() => setDrawer(false)} onRefresh={loadTools} onClear={clearMemory}
        onInspect={(t) => { setInspecting(t); setDrawer(false); }} onDelete={deleteTool}
      />
      <main className="flex min-w-0 flex-1 flex-col pt-[env(safe-area-inset-top)]">
        <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3">
          <button onClick={toggleSidebar} aria-label="Toggle tools" className="rounded-md p-1.5 hover:bg-white/10 transition">
            <Menu className="size-5 md:hidden" /><PanelLeft className="hidden size-5 md:block" />
          </button>
          <h1 className="text-sm font-semibold tracking-tight">Muse</h1>
          <span className={`ml-auto size-2 rounded-full ${error ? "bg-rose-500" : "bg-emerald-400"}`} title={error ? "Backend unreachable" : "Backend connected"} />
        </header>
        <ChatArea messages={messages} streaming={streaming} onSend={send} onAbort={abort} />
      </main>
      {inspecting && <CodeModal tool={inspecting} onClose={() => setInspecting(null)} />}
    </div>
  );
}