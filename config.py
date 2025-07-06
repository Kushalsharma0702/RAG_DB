import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Redis Configuration for Session Management ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# --- Application Configuration ---
SECRET_KEY = os.getenv('SECRET_KEY', 'yM1UtFJsp5xlN0y16PvIMVp_g51FToBMfn66xVeCVZLz6oTv1uHjASmMTrQ5vXRnP-OP1bJ26qdaQ4dq9vB3WTw')
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'True')

# --- Twilio Configuration ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv("TWILIO_CONVERSATIONS_SERVICE_SID")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
TWILIO_TASK_ROUTER_WORKSPACE_SID = os.getenv("TWILIO_TASK_ROUTER_WORKSPACE_SID")
TWILIO_TASK_ROUTER_WORKFLOW_SID = os.getenv("TWILIO_TASK_ROUTER_WORKFLOW_SID")

# --- Database Configuration ---
DB_HOST = os.getenv("DB_HOST", "localhost").strip()
DB_PORT = os.getenv("DB_PORT", "5432").strip()
DB_USER = os.getenv("DB_USER", "postgres").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "Kushal07").strip()
DB_NAME = os.getenv("DB_NAME", "financial_chatbot_db").strip()

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
).strip()

# --- AWS Configuration ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "eu-north-1").strip()
AWS_REGION = AWS_DEFAULT_REGION

# --- AI Model Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-latest").strip()
CLAUDE_MODEL_ID = os.getenv("CLAUDE_MODEL_ID", "").strip()
CLAUDE_INTENT_MODEL_ID = os.getenv("CLAUDE_INTENT_MODEL_ID", "").strip()