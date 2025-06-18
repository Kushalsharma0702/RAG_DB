import os
from dotenv import load_dotenv
load_dotenv()

TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv("TWILIO_CONVERSATIONS_SERVICE_SID")
DATABASE_URL = "postgresql://postgres:Kushal07#@localhost/financial_chatbot_db"