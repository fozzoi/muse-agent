import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    MONGO_URI = os.getenv("MONGO_URI", "")
    TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    E2B_API_KEY = os.getenv("E2B_API_KEY", "")

os.makedirs(Config.TOOLS_DIR, exist_ok=True)
