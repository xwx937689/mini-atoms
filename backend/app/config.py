import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

_raw_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
)
CORS_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
# In production the frontend is served same-origin, so CORS is mostly for local dev.
CORS_ALLOW_ALL = os.getenv("CORS_ALLOW_ALL", "false").lower() == "true"

# Per-IP rate limit for expensive endpoints
RL_GENERATE = int(os.getenv("RL_GENERATE", "6"))
RL_WINDOW = int(os.getenv("RL_WINDOW", "600"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))

DB_PATH = DATA_DIR / "miniatoms.db"


def llm_ready() -> bool:
    return bool(LLM_API_KEY and not LLM_API_KEY.startswith("your_"))
