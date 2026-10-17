# Muse Agent

An autonomous, self-extending AI agent with multi-model routing, dynamic Python tool synthesis, real-time streaming web UI, and Telegram bot integration.

---

## 🌟 Key Features

- **Dynamic Tool Synthesis & Execution:** If the agent doesn't have a tool to answer a request (e.g. system monitoring, disk space, file processing), it autonomously generates, compiles, tests, and saves a standalone Python tool on the fly.
- **Multi-Provider AI Routing:** Automatically routes queries across Groq, Mistral, Gemini, and OpenRouter for optimal speed, coding accuracy, and failover redundancy.
- **Modern Web Interface:** Built with React 19, Vite, Tailwind CSS, and Lucide icons with real-time Server-Sent Events (SSE) streaming and step-by-step trace inspection.
- **Telegram Bot:** Full Telegram interface powered by `python-telegram-bot` with live progress message updates.
- **Tool Inspection & Management:** View generated Python code directly in a syntax-highlighted modal, refresh tools, or delete them via the web UI.

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.14)
- Node.js 18+ & npm

### 2. Environment Setup
Copy the example environment file and add your API keys:
```bash
cp backend/.env.example backend/.env
```
Fill in at least:
- `GROQ_API_KEY` (Free via [console.groq.com](https://console.groq.com))
- `MISTRAL_API_KEY` (Free via [console.mistral.ai](https://console.mistral.ai))
- `GEMINI_API_KEY` (Free via [aistudio.google.com](https://aistudio.google.com))
- `OPENROUTER_API_KEY` (Optional fallback via [openrouter.ai](https://openrouter.ai))
- `TELEGRAM_BOT_TOKEN` (Optional from [@BotFather](https://t.me/BotFather))

### 3. Install Dependencies

**Backend:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..
```

**Frontend:**
```bash
cd web
npm install
cd ..
```

### 4. Run Everything
Start Backend, Web Client, and Telegram Bot in one command:
```bash
./run.sh
```
- **Web App:** [http://localhost:5173](http://localhost:5173)
- **FastAPI Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Telegram Bot:** Active in background if token is configured

---

## 📁 Project Structure

```
muse-agent/
├── backend/
│   ├── core/
│   │   ├── config.py       # Configuration and environment loaders
│   │   ├── router.py       # Multi-model routing and provider failover
│   │   ├── database.py     # Local tool manifest and MongoDB sync
│   │   └── sandbox.py      # Dynamic code generation and tool execution sandbox
│   ├── tools/              # Autonomous generated Python tools
│   ├── bot.py              # Telegram bot runner
│   ├── main.py             # FastAPI backend with SSE streaming
│   └── requirements.txt
├── web/                    # React + Vite + Tailwind CSS frontend
│   ├── src/
│   │   ├── components/     # ChatArea, Sidebar, CodeModal, TraceViewer
│   │   └── hooks/          # useChatStream SSE hook
│   └── package.json
└── run.sh                  # One-click startup script
```
