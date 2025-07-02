from flask import request, jsonify
from flask_socketio import SocketIO
# from database import SessionLocal, RAGDocument, Customer
from database import Session as SessionLocal, RAGDocument, Customer
from database import ClientInteraction
from twilio.rest import Client as TwilioClient
from twilio.base.exceptions import TwilioException
import os
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Twilio configuration
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

# Initialize socketio without Twilio dependency
socketio = SocketIO()

# Lazy initialization of Twilio client
twilio_client = None

def get_twilio_client():
    """Get or initialize the Twilio client"""
    global twilio_client
    if twilio_client is None:
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            logging.error("❌ Twilio credentials are not set in environment variables")
            raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set")
        try:
            twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            logging.info("✅ Twilio client initialized successfully")
        except TwilioException as e:
            logging.error(f"❌ Failed to initialize Twilio client: {e}")
            raise
    return twilio_client

# ✅ Get or create Twilio Conversation for a customer
def get_or_create_conversation(customer_id):
    """Get existing conversation or create a new one for the customer"""
    if not customer_id:
        logging.error("❌ Cannot create conversation: customer_id is required")
        return None
        
    # Use proper session management for thread safety
    db = SessionLocal()
    try:
        client = get_twilio_client()
        
        # Log the customer ID for debugging
        logging.info(f"🔍 Looking for conversation with friendly_name: {customer_id}")
        
        # First try to find an existing conversation
        conversations = client.conversations.conversations.list(limit=20)
        for convo in conversations:
            logging.debug(f"Checking conversation: {convo.sid} with friendly_name: {convo.friendly_name}")
            if convo.friendly_name == customer_id:
                logging.info(f"✅ Found existing conversation: {convo.sid}")
                return convo.sid
                
        # Create a new conversation if none exists
        logging.info(f"🏆 Creating new conversation for customer: {customer_id}")
        new_convo = client.conversations.conversations.create(friendly_name=customer_id)
        logging.info(f"✅ Created new conversation: {new_convo.sid}")
        
        # Track this in the database if needed
        # db.add(ClientInteraction(...))
        # db.commit()
        
        return new_convo.sid
    except Exception as e:
        db.rollback()
        logging.error(f"❌ Error in get_or_create_conversation: {e}")
        return None
    finally:
        db.close()
