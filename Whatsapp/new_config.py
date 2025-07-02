import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Application Configuration ---
# Generate a strong, random key for production:
# python -c 'import os; print(os.urandom(24).hex())'
SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'a_very_insecure_default_secret_key_change_this_in_prod')
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False') # Set to 'False' for production

# --- Twilio Configuration ---
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
# This is typically your Twilio SMS-enabled number or the WhatsApp sender number (e.g., whatsapp:+1234567890)
TWILIO_PHONE = os.getenv("TWILIO_PHONE", "") 
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv("TWILIO_CONVERSATIONS_SERVICE_SID", "")
TWILIO_TASK_ROUTER_WORKFLOW_SID = os.getenv("TWILIO_TASK_ROUTER_WORKFLOW_SID", "")
TWILIO_TASK_ROUTER_WORKSPACE_SID = os.getenv("TWILIO_TASK_ROUTER_WORKSPACE_SID", "")

# --- Database Configuration ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "") # IMPORTANT: Do NOT set a default password here in production
DB_NAME = os.getenv("DB_NAME", "financial_chatbot_db")

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# --- AWS Configuration for Bedrock ---
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1") # Example region, set your actual region

# --- AI Model Configuration ---
# Example Bedrock Model IDs (replace with your specific ARNs if different)
BEDROCK_TEXT_MODEL_ID = os.getenv("BEDROCK_TEXT_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
BEDROCK_EMBEDDING_MODEL_ID = os.getenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v1")
BEDROCK_INTENT_MODEL_ID = os.getenv("BEDROCK_INTENT_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0") # Often a faster, cheaper model for intent
