import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Application Configuration ---
SECRET_KEY = os.getenv('SECRET_KEY', 'yM1UtFJsp5xlN0y16PvIMVp_g51FToBMfn66xVeCVZLz6oTv1uHjASmMTrQ5vXRnP-OP1bJ26qdaQ4dq9vB3WTw')
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'True') # Keep as string, Flask converts it

# --- Twilio Configuration ---
# Applying .strip() to all fetched environment variables for robustness
# This removes any leading/trailing whitespace, which often causes "404 not found" errors
TWILIO_SID = os.getenv("TWILIO_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_PHONE = os.getenv("TWILIO_PHONE", "").strip()
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv("TWILIO_CONVERSATIONS_SERVICE_SID", "").strip()
TWILIO_TASK_ROUTER_WORKFLOW_SID = os.getenv("TWILIO_TASK_ROUTER_WORKFLOW_SID", "").strip()
TWILIO_TASK_ROUTER_WORKSPACE_SID = os.getenv("TWILIO_TASK_ROUTER_WORKSPACE_SID", "").strip() # This is the critical one!

# --- Database Configuration ---
DB_HOST = os.getenv("DB_HOST", "localhost").strip()
DB_PORT = os.getenv("DB_PORT", "5432").strip()
DB_USER = os.getenv("DB_USER", "postgres").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "Kushal07#").strip()
DB_NAME = os.getenv("DB_NAME", "financial_chatbot_db").strip()

# Construct database URL
# Using .strip() for components, or for the whole URL if directly from an env var
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
).strip() # Apply strip to the final DATABASE_URL as well

# --- AWS Configuration ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "eu-north-1").strip()
AWS_REGION = AWS_DEFAULT_REGION # Ensure consistency if both are used

# --- AI Model Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash-latest").strip()
CLAUDE_MODEL_ID = os.getenv("CLAUDE_MODEL_ID", "").strip()
CLAUDE_INTENT_MODEL_ID = os.getenv("CLAUDE_INTENT_MODEL_ID", "").strip()

# --- Optional: Logging Configuration ---
# LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').strip()
# LOG_FILE_PATH = os.getenv('LOG_FILE_PATH', 'logs/chatbot.log').strip()