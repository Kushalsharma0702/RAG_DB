from flask import Flask, request, jsonify, session, send_from_directory, render_template
from dotenv import load_dotenv
import os
import uuid
import logging
import json
from datetime import datetime, date
from flask_cors import CORS
from twilio.rest import Client
from flask_socketio import SocketIO, emit, join_room
from database import ClientInteraction, Session as DatabaseSession, RAGDocument
from sqlalchemy.orm import Session
from app_socketio import get_or_create_conversation
from otp_manager import send_otp
from bedrock_client import generate_response, get_chat_summary, get_embedding, get_intent_from_text
from intent_classifier import classify_intent
from database import (
    fetch_customer_by_account,
    save_chat_interaction,
    save_unresolved_chat,
    get_last_three_chats,
    create_tables
)
from rag_utils import fetch_data
from db_migration import run_migration
from config import (
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID, TWILIO_PHONE,
    TWILIO_TASK_ROUTER_WORKSPACE_SID, TWILIO_TASK_ROUTER_WORKFLOW_SID,
    REDIS_HOST, REDIS_PORT, REDIS_DB
)
from twilio_chat import create_conversation, send_message_to_conversation, create_task_for_handoff
from twilio.twiml.messaging_response import MessagingResponse
from session_manager import session_manager
from twilio.twiml.voice_response import VoiceResponse, Gather
from functools import wraps
from sqlalchemy import text
import boto3
from botocore.config import Config

# --- Outbound Call Configuration ---
AGENT_PHONE_NUMBER = "+917983394461"
NGROK_URL = os.getenv('NGROK_URL')
# Around line ~50 add this:
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE')  # Get from the TWILIO_PHONE env var

# Or directly replace all instances of TWILIO_PHONE_NUMBER with TWILIO_PHONE

# --- In-Memory Call Tasks Storage ---
call_tasks = {}

LANG_CONFIG = {
    '1': {'code': 'en-IN', 'name': 'English', 'voice': 'Polly.Raveena'},
    '2': {'code': 'hi-IN', 'name': 'Hindi', 'voice': 'Polly.Raveena'},
    '3': {'code': 'te-IN', 'name': 'Telugu', 'voice': 'Polly.Raveena'}
}

def _handle_escalation(customer_id: str, phone_number: str, chat_history: list, channel: str = 'web'):
    """
    Handles the full escalation process: summary, Twilio Task creation, and agent notification.
    """
    try:
        # 1. Generate a summary of the conversation
        summary_text = get_chat_summary(chat_history)
        
        # 2. Create or get the Twilio Conversation
        conversation_sid = create_conversation(str(customer_id))
        if not conversation_sid:
            logging.error(f"Escalation failed for {customer_id}: Could not create Twilio Conversation.")
            return None, "Failed to create a chat channel for the agent."

        # 3. Send context to the Twilio Conversation
        send_message_to_conversation(conversation_sid, "System", f"Handoff from {channel} for user {customer_id}.\n\nSummary:\n{summary_text}")
        send_message_to_conversation(conversation_sid, "System", "--- Recent Chat History ---")
        for msg in chat_history[-5:]:
            sender = msg.get('sender', 'Unknown')
            message_text = msg.get('message', '(empty message)')
            send_message_to_conversation(conversation_sid, sender.capitalize(), message_text)
        send_message_to_conversation(conversation_sid, "System", "--- End of History ---")

        # 4. Create a Task in Twilio TaskRouter
        task_sid = create_task_for_handoff(
            customer_id=customer_id,
            phone_number=phone_number,
            summary=summary_text,
            recent_messages=chat_history[-5:],
            conversation_sid=conversation_sid
        )

        if task_sid:
            # 5. Save unresolved chat to DB
            summary_embedding = get_embedding(summary_text)
            if summary_embedding:
                # FIX: Call save_unresolved_chat and pass the correct channel source
                save_unresolved_chat(
                    customer_id=customer_id,
                    summary=summary_text,
                    embedding=summary_embedding,
                    task_id=task_sid,
                    source=channel  # Use the channel ('web' or 'whatsapp')
                )

            # 6. Notify agent dashboard via Socket.IO
            socketio.emit('new_escalated_chat', {
                'customer_id': customer_id,
                'summary': summary_text,
                'task_id': task_sid,
                'channel': channel
            }, room='agent_room')
            
            logging.info(f"Successfully escalated chat for {customer_id}. Task SID: {task_sid}")
            return task_sid, "Escalation successful."
        else:
            logging.error(f"Escalation failed for {customer_id}: Could not create Twilio Task.")
            return None, "Failed to create an agent task."
    except Exception as e:
        logging.error(f"Exception during escalation for {customer_id}: {e}")
        return None, "An internal error occurred while connecting to an agent."

load_dotenv()

# Update Flask app initialization with correct template_folder
app = Flask(__name__, 
           template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend'),
           static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
app.secret_key = os.getenv("SECRET_KEY", "a_strong_default_secret_key")
CORS(app, supports_credentials=True)

# Initialize Socket.IO with your Flask app
socketio = SocketIO(app, cors_allowed_origins="*")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

with app.app_context():
    create_tables()
    try:
        run_migration()
        print("Database setup completed successfully")
    except Exception as e:
        print(f"Warning: Database migration encountered an issue: {e}")
        print("The application will continue, but some features may not work correctly.")

@app.route('/')
def serve_frontend():
    try:
        # Create or get web session
        if 'web_session_id' not in session:
            web_session_id = str(uuid.uuid4())
            session['web_session_id'] = web_session_id
            session_manager.create_session(web_session_id, 'web')
            logging.info(f"Created new web session: {web_session_id}")
        else:
            web_session_id = session['web_session_id']
            # Ensure session exists in Redis, recreate if lost
            if not session_manager.get_session(web_session_id, 'web'):
                session_manager.create_session(web_session_id, 'web')
                logging.warning(f"Recreated missing Redis session for web_session_id: {web_session_id}")

        # Check if the request is for the outbound system
        if 'outbound' in request.args:
            try:
                return render_template('outbound.html')
            except Exception as e:
                logging.error(f"Error serving outbound.html: {e}")
                return f"<h1>Error loading outbound system: {str(e)}</h1>", 500
        
        # Default to the regular chatbot interface
        index_path = os.path.join(app.static_folder, 'index.html')
        if not os.path.exists(index_path):
            logging.error(f"index.html not found at {index_path}")
            return f"<h1>Error: index.html not found at {index_path}</h1>", 404
        
        logging.info(f"Serving index.html from {index_path}")
        return send_from_directory(app.static_folder, 'index.html')
    except Exception as e:
        logging.error(f"Error serving frontend: {e}")
        return f"<h1>Error: {str(e)}</h1>", 500

@app.route('/send_otp', methods=['POST'])
def otp_request():
    try:
        account_id_input = request.json.get("account_id")
        if not account_id_input:
            logging.warning("❌ OTP request failed: No account ID provided.")
            return jsonify({"status": "error", "message": "Account ID is required."}), 400

        web_session_id = session.get('web_session_id')
        if not web_session_id:
            return jsonify({"status": "error", "message": "Session not found. Please refresh the page."}), 400

        # Get session data
        session_data = session_manager.get_session(web_session_id, 'web')
        if not session_data:
            return jsonify({"status": "error", "message": "Session expired. Please refresh the page."}), 400

        # Add to conversation history
        session_manager.add_to_conversation_history(web_session_id, {
            'sender': 'user',
            'message': f"Account ID: {account_id_input}",
            'stage': 'account_id_entry'
        }, 'web')

        # Fetch customer account
        customer_account = fetch_customer_by_account(account_id_input)
        if not customer_account:
            logging.warning(f"❌ OTP request failed: Account ID {account_id_input} not found.")
            reply = "Account ID not found. Please try again or contact support."
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'account_id_not_found'
            }, 'web')
            
            return jsonify({"status": "error", "message": reply}), 404

        customer_id = customer_account['customer_id']
        phone_number = customer_account['phone_number']

        # Update session with customer info
        session_manager.update_session(web_session_id, {
            'customer_id': customer_id,
            'account_id': account_id_input,
            'phone_number': phone_number,
            'stage': 'otp_requested'
        }, 'web')

        # Send OTP
        otp = send_otp(phone_number)
        if otp:
            # Store OTP in session
            session_manager.set_otp(web_session_id, otp, 'web')
            
            logging.info(f"🏆 OTP sent to {phone_number} for account_id={account_id_input}")
            reply = f"OTP sent to number ending with {phone_number[-4:]}"
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'otp_sent'
            }, 'web')
            
            return jsonify({"status": "success", "message": reply, "phone_number": phone_number})
        else:
            logging.error(f"❌ Failed to send OTP to {phone_number}")
            reply = "Failed to send OTP. Please try again."
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'otp_send_failed'
            }, 'web')
            
            return jsonify({"status": "error", "message": reply}), 500
            
    except Exception as e:
        logging.error(f"Error in OTP request: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route('/verify_otp', methods=['POST'])
def otp_verification():
    try:
        user_otp = request.json.get("otp")
        web_session_id = session.get('web_session_id')
        
        if not web_session_id or not user_otp:
            return jsonify({"status": "error", "message": "Session or OTP is required."}), 400

        # Get session data
        session_data = session_manager.get_session(web_session_id, 'web')
        if not session_data:
            return jsonify({"status": "error", "message": "Session expired. Please refresh the page."}), 400

        # Add to conversation history
        session_manager.add_to_conversation_history(web_session_id, {
            'sender': 'user',
            'message': f"OTP: {user_otp}",
            'stage': 'otp_attempt'
        }, 'web')

        # Validate OTP
        is_valid, message, should_regenerate = session_manager.validate_otp(web_session_id, user_otp, 'web')
        
        if is_valid:
            logging.info(f"🏆 OTP verified for session {web_session_id}")
            reply = "OTP validated successfully. You can now ask your questions."
            
            # Update session stage
            session_manager.update_session(web_session_id, {
                'stage': 'authenticated'
            }, 'web')
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'otp_verified'
            }, 'web')
            
            return jsonify({
                "status": "success", 
                "message": reply,
                "customer_id": session_data.get('customer_id')
            })
        else:
            logging.warning(f"❌ OTP verification failed for session {web_session_id}")
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': message,
                'stage': 'otp_failed'
            }, 'web')
            
            return jsonify({"status": "error", "message": message}), 401
            
    except Exception as e:
        logging.error(f"Error in OTP verification: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get("message")
        chat_history = request.json.get("chat_history", [])
        web_session_id = session.get('web_session_id')
        
        if not web_session_id:
            return jsonify({"status": "error", "message": "Session not found. Please refresh the page."}), 400

        # Get session data
        session_data = session_manager.get_session(web_session_id, 'web')
        if not session_data:
            return jsonify({"status": "error", "message": "Session expired. Please refresh the page."}), 400

        customer_id = session_data.get('customer_id')
        account_id = session_data.get('account_id')
        authenticated = session_data.get('authenticated', False)

        # Add user message to conversation history
        session_manager.add_to_conversation_history(web_session_id, {
            'sender': 'user',
            'message': user_message,
            'stage': 'chat_message'
        }, 'web')

        logging.info(f"🏆 Chat request received for account_id={account_id}, customer_id={customer_id}")

        # Handle unauthenticated users
        if not authenticated:
            intent = classify_intent(user_message)
            if intent == "unclear":
                try:
                    intent = get_intent_from_text([{"sender": "user", "content": user_message}])
                except Exception as e:
                    logging.error(f"Error with ML intent classification: {e}")
            
            if intent in ['emi', 'balance', 'loan']:
                reply = "Understood. To proceed, please enter your Account ID:"
                session_manager.update_session(web_session_id, {
                    'intent': intent,
                    'pending_message': user_message,
                    'stage': 'awaiting_account_id'
                }, 'web')
            else:
                reply = "Hello! I am your financial assistant. You can ask me about your EMI, account balance, or loan details. You can also select an option below."
                session_manager.update_session(web_session_id, {
                    'stage': 'initial_greeting'
                }, 'web')
            
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': session_data.get('stage', 'initial')
            }, 'web')
            
            return jsonify({"status": "success", "reply": reply})

        # Handle authenticated users
        pending_intent = session_data.get('intent')
        query_type = pending_intent if pending_intent else classify_intent(user_message)
        
        if query_type == 'unclear':
            try:
                query_type = get_intent_from_text([{"sender": "user", "content": user_message}])
            except Exception as e:
                logging.error(f"Error with ML intent classification: {e}")

        logging.info(f"🏆 Using query_type={query_type} for account_id={account_id}")

        if query_type == 'unclear':
            reply = "I'm not sure what financial information you're looking for. I can provide information about EMI, account balance, or loan details."
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'intent_unclear'
            }, 'web')
            return jsonify({"status": "success", "reply": reply, "needs_agent": True})

        # Fetch data
        data = fetch_data(query_type, account_id)
        if not data:
            reply = f"I couldn't find any information for your {query_type} query. This could be because the data doesn't exist in our system or there might be an issue accessing it."
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'query_failed'
            }, 'web')
            return jsonify({"status": "success", "reply": reply, "needs_agent": True})

        # Check for complex queries
        if "out_of_scope" in user_message.lower() or any(word in user_message.lower() for word in ["complex", "difficult", "complicated", "help", "agent", "human", "talk", "speak"]):
            reply = "This seems like a complex query that might be better handled by one of our human agents."
            session_manager.add_to_conversation_history(web_session_id, {
                'sender': 'bot',
                'message': reply,
                'stage': 'complex_query'
            }, 'web')
            return jsonify({"status": "success", "reply": reply, "needs_agent": True})

        # Generate response
        logging.info(f"🏆 Data fetched for {query_type} (account_id={account_id})")
        
        # FIX: Convert chat history to the format expected by the Bedrock client
        formatted_history = []
        for msg in chat_history:
            # Ensure message content is not empty
            if msg.get('message'):
                formatted_history.append({
                    "role": "user" if msg.get('sender') == 'user' else "assistant",
                    "content": msg.get('message')
                })

        reply = generate_response(query_type, data, formatted_history)
        
        session_manager.add_to_conversation_history(web_session_id, {
            'sender': 'bot',
            'message': reply,
            'stage': 'query_resolved'
        }, 'web')
        
        # Clear pending intent after successful response
        session_manager.update_session(web_session_id, {'intent': None}, 'web')

        return jsonify({"status": "success", "reply": reply})
        
    except Exception as e:
        logging.error(f"Error in chat: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route("/summarize_chat", methods=["POST"])
def summarize_chat_route():
    """
    Legacy route to handle requests from older client-side code.
    This now redirects internally to the main connect_agent logic.
    """
    logging.warning("Received request on deprecated /summarize_chat endpoint. Forwarding to /connect_agent.")
    return connect_agent()

@app.route('/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook():
    try:
        incoming_msg = request.values.get('Body', '').strip()
        whatsapp_phone_number = request.values.get('From', '').replace('whatsapp:', '')
        resp = MessagingResponse()
        response_text = "" # Initialize an empty string for the response

        logging.info(f"WhatsApp message received from {whatsapp_phone_number}: {incoming_msg}")

        # Get or create session
        session_data = session_manager.get_session(whatsapp_phone_number, 'whatsapp')
        if not session_data:
            session_manager.create_session(whatsapp_phone_number, 'whatsapp')
            session_data = session_manager.get_session(whatsapp_phone_number, 'whatsapp')

        # Add user message to conversation history
        session_manager.add_to_conversation_history(whatsapp_phone_number, {
            'sender': 'user',
            'message': incoming_msg
        }, 'whatsapp')

        current_stage = session_data.get('stage', 'greeting')
        logging.info(f"Current stage for {whatsapp_phone_number}: {current_stage}")

        # Handle different stages, assigning the reply to `response_text`
        if current_stage == 'greeting' or incoming_msg.lower() in ['hi', 'hello', 'hey', 'start']:
            response_text = "Welcome! How can I help you today?\n1. Know your EMI\n2. Account Balance\n3. Know your Loan Amount\n(Reply with the number of your choice)"
            session_manager.update_session(whatsapp_phone_number, {'stage': 'menu'}, 'whatsapp')

        elif current_stage == 'menu':
            intent = None
            if incoming_msg == '1':
                intent = 'emi'
            elif incoming_msg == '2':
                intent = 'balance'
            elif incoming_msg == '3':
                intent = 'loan'
            
            if intent:
                response_text = "Please enter your Account ID."
                session_manager.update_session(whatsapp_phone_number, {
                    'intent': intent,
                    'stage': 'account_id'
                }, 'whatsapp')
            else:
                response_text = "Please select a valid option (1, 2, or 3):\n1. Know your EMI\n2. Account Balance\n3. Know your Loan Amount"

        elif current_stage == 'account_id':
            account_info = fetch_customer_by_account(incoming_msg)
            if account_info:
                session_manager.update_session(whatsapp_phone_number, {
                    'account_id': incoming_msg,
                    'customer_id': account_info['customer_id'],
                    'phone_number': account_info['phone_number'],
                    'stage': 'otp_requested'
                }, 'whatsapp')
                
                otp = send_otp(account_info['phone_number'])
                if otp:
                    session_manager.set_otp(whatsapp_phone_number, otp, 'whatsapp')
                    response_text = "OTP sent to your registered mobile number! Please enter the 6-digit OTP."
                    session_manager.update_session(whatsapp_phone_number, {'stage': 'otp'}, 'whatsapp')
                else:
                    response_text = "Failed to send OTP. Please try again later."
            else:
                response_text = "Invalid Account ID. Please try again."

        elif current_stage == 'otp':
            is_valid, otp_message, should_regenerate = session_manager.validate_otp(whatsapp_phone_number, incoming_msg, 'whatsapp')
            if is_valid:
                session_data = session_manager.get_session(whatsapp_phone_number, 'whatsapp')
                intent = session_data.get('intent')
                account_id = session_data.get('account_id')
                
                data = fetch_data(intent, account_id)
                if data:
                    answer = generate_response(intent, data, [])
                    response_text = f"{answer}\n\nPlease share your feedback: 👍 or 👎"
                    session_manager.update_session(whatsapp_phone_number, {'stage': 'feedback'}, 'whatsapp')
                else:
                    response_text = "No data found. Please check your account or try again later."
                    session_manager.update_session(whatsapp_phone_number, {'stage': 'greeting'}, 'whatsapp')
            else:
                response_text = f"{otp_message}"
                if should_regenerate:
                    session_manager.update_session(whatsapp_phone_number, {'stage': 'account_id'}, 'whatsapp')

        elif current_stage == 'feedback':
            if incoming_msg in ['👍', 'thumbs up', 'good', 'yes', '1']:
                response_text = "Thank you for using our service! If you have more queries, just say hi."
                session_manager.delete_session(whatsapp_phone_number, 'whatsapp')
            elif incoming_msg in ['👎', 'thumbs down', 'bad', 'no', '2']:
                session_data = session_manager.get_session(whatsapp_phone_number, 'whatsapp')
                customer_id = session_data.get('customer_id')
                phone_number = session_data.get('phone_number')
                chat_history = session_data.get('conversation_history', [])

                if customer_id:
                    # FIX: Call the escalation handler for WhatsApp
                    task_sid, message = _handle_escalation(
                        customer_id=customer_id,
                        phone_number=phone_number,
                        chat_history=chat_history,
                        channel='whatsapp'  # Specify the channel
                    )
                    if task_sid:
                        session_manager.escalate_session(whatsapp_phone_number, 'feedback', 'whatsapp')
                        response_text = "Thank you for your feedback. We will have an agent contact you shortly."
                    else:
                        response_text = "Sorry, we are unable to connect you to an agent at this time."
                else:
                    # Handle case where customer_id is missing
                    response_text = "We couldn't identify your account to escalate. Please start over."
                    session_manager.delete_session(whatsapp_phone_number, 'whatsapp')

            else:
                response_text = "Please reply with 👍 for satisfied or 👎 for unsatisfied."

        elif current_stage == 'escalated':
            response_text = "Your request has been escalated to our support team. An agent will contact you soon."

        else:
            response_text = "Welcome! How can I help you today?\n1. Know your EMI\n2. Account Balance\n3. Know your Loan Amount\n(Reply with the number of your choice)"
            session_manager.update_session(whatsapp_phone_number, {'stage': 'menu'}, 'whatsapp')

        # Set the message body using the response_text variable
        resp.message(response_text)

        # Add bot response to conversation history
        session_manager.add_to_conversation_history(whatsapp_phone_number, {
            'sender': 'bot',
            'message': response_text
        }, 'whatsapp')

        logging.info(f"Sending WhatsApp response to {whatsapp_phone_number}: {response_text}")
        return str(resp)
        
    except Exception as e:
        logging.error(f"Error in WhatsApp webhook: {e}")
        resp = MessagingResponse()
        msg = resp.message()
        msg.body("Sorry, something went wrong. Please try again later.")
        return str(resp)

@app.route("/connect_agent", methods=["POST"])
def connect_agent():
    try:
        chat_history = request.json.get("chat_history", [])
        web_session_id = session.get('web_session_id')
        
        if not web_session_id:
            return jsonify({"status": "error", "message": "Session not found. Please refresh the page."}), 400

        session_data = session_manager.get_session(web_session_id, 'web')
        if not session_data:
            return jsonify({"status": "error", "message": "Session expired. Please refresh the page."}), 400

        customer_id = session_data.get('customer_id')
        customer_phone_number = session_data.get('phone_number')
        
        if not customer_id:
            return jsonify({"status": "error", "message": "Cannot escalate without a customer ID. Please verify your account first."}), 400

        task_sid, message = _handle_escalation(
            customer_id=customer_id,
            phone_number=customer_phone_number,
            chat_history=chat_history,
            channel='web'
        )

        if task_sid:
            session_manager.escalate_session(web_session_id, 'manual_request', 'web')
            return jsonify({"status": "success", "message": "You are being connected to an agent. They will have your chat history."})
        else:
            return jsonify({"status": "error", "message": message}), 500
            
    except Exception as e:
        logging.error(f"Error in connect_agent: {e}")
        return jsonify({"status": "error", "message": "An internal error occurred while connecting to an agent."}), 500

# Add session cleanup endpoint
@app.route('/cleanup_sessions', methods=['POST'])
def cleanup_sessions():
    try:
        session_manager.cleanup_expired_sessions()
        return jsonify({"status": "success", "message": "Session cleanup completed"})
    except Exception as e:
        logging.error(f"Error in session cleanup: {e}")
        return jsonify({"status": "error", "message": "Cleanup failed"}), 500

# Add session status endpoint
@app.route('/session_status', methods=['GET'])
def session_status():
    try:
        web_session_id = session.get('web_session_id')
        if not web_session_id:
            return jsonify({"status": "error", "message": "No session found"}), 400
        
        session_data = session_manager.get_session(web_session_id, 'web')
        if not session_data:
            return jsonify({"status": "error", "message": "Session expired"}), 400
        
        return jsonify({
            "status": "success",
            "session_id": session_data.get('session_id'),
            "authenticated": session_data.get('authenticated', False),
            "customer_id": session_data.get('customer_id'),
            "stage": session_data.get('stage'),
            "escalated": session_data.get('escalated', False)
        })
    except Exception as e:
        logging.error(f"Error getting session status: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# Keep your existing Socket.IO handlers and other routes...
@socketio.on('connect')
def handle_connect():
    logging.info(f"Client connected to Socket.IO: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logging.info(f"Client disconnected from Socket.IO: {request.sid}")

@socketio.on('join_customer_room')
def handle_join_customer_room(data):
    customer_id = data.get('customer_id')
    if customer_id:
        room_name = f'customer_{customer_id}'
        join_room(room_name)
        print(f"Customer {customer_id} joined room: {room_name}")
        emit('room_joined', {'room': room_name})

@socketio.on('join_agent_room')
def handle_join_agent_room():
    join_room('agent_room')
    emit('room_joined', {'room': 'agent_room'})

# Add your other existing routes here...
# (agent dashboard, chat history, etc.)
@app.route('/agent-dashboard')
def agent_dashboard():
    return send_from_directory(app.static_folder, 'agent_dashboard.html')

@app.route('/agent/chat-interface')
def agent_chat_interface():
    return send_from_directory(app.static_folder, 'agent_chat_interface.html')

@app.route('/agent/unresolved_sessions')
def get_unresolved_sessions():
    try:
        from database import Session, RAGDocument, Customer
        
        db = Session()
        try:
            # FIX: Join RAGDocument with Customer to fetch the phone number and full_name
            unresolved_docs = db.query(
                RAGDocument,
                Customer.phone_number,
                Customer.full_name
            ).join(
                Customer, RAGDocument.customer_id == Customer.customer_id
            ).filter(
                RAGDocument.status != 'resolved'
            ).order_by(RAGDocument.created_at.desc()).limit(50).all()
            
            sessions = []
            for doc, phone_number, full_name in unresolved_docs:
                sessions.append({
                    'document_id': str(doc.document_id),
                    'customer_id': doc.customer_id,
                    'full_name': full_name,
                    'document_text': doc.document_text,
                    'created_at': doc.created_at.isoformat() if doc.created_at else datetime.now().isoformat(),
                    'status': doc.status or 'pending',
                    'task_id': doc.task_id if hasattr(doc, 'task_id') else None,
                    'phone_number': phone_number,
                    'source': doc.source
                })
            
            return jsonify({
                'status': 'success',
                'sessions': sessions
            })
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Error getting unresolved sessions: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to load sessions'
        }), 500

@app.route('/agent/get_chat_history/<customer_id>')
def get_chat_history(customer_id):
    try:
        from database import Session, ClientInteraction
        
        db = Session()
        try:
            # Get chat history for customer
            interactions = db.query(ClientInteraction).filter(
                ClientInteraction.customer_id == customer_id
            ).order_by(ClientInteraction.created_at.asc()).limit(100).all()
            
            messages = []
            for interaction in interactions:
                messages.append({
                    'message': interaction.message_text,
                    'sender': interaction.sender,
                    'timestamp': interaction.created_at.isoformat() if interaction.created_at else datetime.now().isoformat()
                })
            
            return jsonify({
                'status': 'success',
                'messages': messages
            })
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Error getting chat history: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to load chat history'
        }), 500

@app.route('/agent/send_message', methods=['POST'])
def agent_send_message():
    try:
        data = request.json
        customer_id = data.get('customer_id')
        message = data.get('message')
        
        if not customer_id or not message:
            return jsonify({'status': 'error', 'message': 'Customer ID and message are required'}), 400
        
        # Save message to database
        from database import Session, ClientInteraction
        db = Session()
        try:
            interaction = ClientInteraction(
                session_id=str(uuid.uuid4()),
                customer_id=customer_id,
                sender='agent',
                message_text=message,
                created_at=datetime.now().isoformat()
            )
            db.add(interaction)
            db.commit()
            
            # Send via Socket.IO to customer
            socketio.emit('new_message', {
                'customer_id': customer_id,
                'message': message,
                'sender': 'agent',
                'timestamp': datetime.now().isoformat()
            }, room=f'customer_{customer_id}')
            
            return jsonify({
                'status': 'success',
                'message': 'Message sent successfully'
            })
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Error sending agent message: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to send message'
        }), 500

@app.route('/agent/mark_as_resolved', methods=['POST'])
def mark_as_resolved():
    try:
        data = request.json
        task_id = data.get('task_id')
        
        if not task_id:
            return jsonify({'status': 'error', 'message': 'Task ID is required'}), 400
        
        # Update status in database
        from database import Session, RAGDocument
        db = Session()
        try:
            # Update by document_id (which might be passed as task_id)
            document = db.query(RAGDocument).filter(RAGDocument.document_id == task_id).first()
            if document:
                document.status = 'resolved'
                db.commit()
                return jsonify({'status': 'success', 'message': 'Task marked as resolved'})
            else:
                return jsonify({'status': 'error', 'message': 'Task not found'}), 404
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Error marking task as resolved: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to mark as resolved'
        }), 500

@app.route('/agent/get_or_create_conversation')
def get_or_create_conversation_route():
    try:
        customer_id = request.args.get('customer_id')
        if not customer_id:
            return jsonify({'success': False, 'error': 'Customer ID is required'}), 400
        
        conversation_sid = create_conversation(customer_id)
        if conversation_sid:
            return jsonify({
                'success': True,
                'conversation_sid': conversation_sid
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to create conversation'}), 500
            
    except Exception as e:
        logging.error(f"Error in get_or_create_conversation: {e}")
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/agent/update_task_status', methods=['POST'])
def update_task_status():
    try:
        data = request.json
        document_id = data.get('document_id')
        status = data.get('status')
        
        if not document_id or not status:
            return jsonify({'status': 'error', 'message': 'Document ID and status are required'}), 400
        
        # Update status in database
        from database import Session, RAGDocument
        db = Session()
        try:
            document = db.query(RAGDocument).filter(RAGDocument.document_id == document_id).first()
            if document:
                document.status = status
                db.commit()
                return jsonify({'status': 'success', 'message': f'Status updated to {status}'})
            else:
                return jsonify({'status': 'error', 'message': 'Document not found'}), 404
        finally:
            db.close()
            
    except Exception as e:
        logging.error(f"Error updating task status: {e}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to update status'
        }), 500

# --- Outbound Call Helper Functions ---

def fetch_high_risk_customers():
    """
    Fetches high-risk customers from the database who need collection calls.
    """
    try:
        db = DatabaseSession()
        
        # Diagnostic query to understand what we have
        diagnostic_query = text("""
            SELECT 
                (SELECT COUNT(*) FROM customer) as total_customers,
                (SELECT COUNT(*) FROM collectiontask) as total_tasks,
                (SELECT COUNT(*) FROM riskscore) as total_risk_scores,
                (SELECT COUNT(*) FROM collectiontask WHERE status = 'pending') as pending_tasks,
                (SELECT COUNT(*) FROM riskscore WHERE risk_segment = 'High') as high_risk_customers
        """)
        
        diagnostic = db.execute(diagnostic_query).fetchone()
        print(f"Database stats: {diagnostic}")
        
        # Fixed query to handle multiple risk scores per customer and avoid missing EMI data
        query = text("""
            WITH latest_risk AS (
                SELECT customer_id, risk_segment, score,
                    ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY risk_date DESC) as rn
                FROM riskscore
            )
            SELECT 
                ct.task_id, 
                c.customer_id, 
                c.full_name AS customer_name, 
                c.phone_number AS customer_phone_number,
                l.loan_id AS loan_id_full,
                RIGHT(l.loan_id, 4) AS loan_last4,
                e.amount_due AS emi_amount,
                TO_CHAR(e.due_date, 'DD Month') AS due_date,
                ct.status,
                ct.priority_level
            FROM 
                collectiontask ct
            JOIN 
                customer c ON ct.customer_id = c.customer_id
            JOIN 
                loan l ON ct.loan_id = l.loan_id
            LEFT JOIN 
                emi e ON l.loan_id = e.loan_id
            LEFT JOIN 
                latest_risk rs ON c.customer_id = rs.customer_id AND rs.rn = 1
            WHERE 
                ct.status = 'pending'
                AND rs.risk_segment IN ('High', 'Critical', 'high', 'critical')
            ORDER BY 
                rs.score DESC,
                ct.priority_level
            LIMIT 100;
        """)
        
        result = db.execute(query)
        customers = []
        
        for row in result:
            customers.append({
                'task_id': str(row.task_id),
                'customer_id': row.customer_id,
                'customer_name': row.customer_name,
                'customer_phone_number': row.customer_phone_number,
                'loan_id_full': row.loan_id_full,
                'loan_last4': row.loan_last4,
                'emi_amount': f'₹{row.emi_amount:,.0f}' if row.emi_amount else '₹0',
                'due_date': row.due_date,
                'status': row.status or 'pending',
                'current_language': '1'
            })
        
        if not customers:
            print("⚠️ No high-risk customers with pending tasks found in the database.")
            print("You may need to reset tasks to 'pending' status by using the /reset-tasks endpoint")
            print("Or access /start-campaign?reset=true to reset tasks automatically")
        else:
            print(f"Found {len(customers)} high-risk customers with pending tasks")
            
        db.close()
        return customers
    
    except Exception as e:
        print(f"❌ Database error in fetch_high_risk_customers: {e}")
        return []

def update_task_status_in_db(task_id, status):
    """
    Updates a task's status in the database.
    """
    try:
        db = DatabaseSession()
        update_query = text("""
            UPDATE CollectionTask 
            SET status = :status
            WHERE task_id = :task_id
            RETURNING task_id
        """)
        
        result = db.execute(update_query, {
            'task_id': task_id,
            'status': status
        })
        
        updated = result.rowcount > 0
        db.commit()
        db.close()
        
        if updated:
            print(f"✅ Task {task_id} status updated to '{status}' in database")
        else:
            print(f"⚠️ Task {task_id} not found for status update")
        
        return updated
    except Exception as e:
        print(f"❌ Error updating task status: {e}")
        return False

def record_call_outcome(task_id, outcome_type, notes=None, ptp=False, ptp_date=None):
    """
    Records the outcome of a collection call in the CallOutcome table.
    """
    try:
        db = DatabaseSession()
        query = text("""
            INSERT INTO CallOutcome 
            (task_id, outcome_type, ptp, ptp_date, notes) 
            VALUES 
            (:task_id, :outcome_type, :ptp, :ptp_date, :notes)
        """)
        
        db.execute(query, {
            'task_id': task_id, 
            'outcome_type': outcome_type, 
            'ptp': ptp, 
            'ptp_date': ptp_date,
            'notes': notes
        })
        db.commit()
        db.close()
        print(f"✅ Call outcome recorded for task {task_id}: {outcome_type}")
    except Exception as e:
        print(f"❌ Database error recording call outcome: {e}")

def translate_text(text, target_lang_key, target_lang_name):
    """
    Translates text to the target language using AWS Bedrock.
    """
    try:
        # Return original text if target is English or if no translation needed
        if target_lang_key == '1': 
            return text
            
        # If Bedrock client isn't available, return with a note
        if not bedrock_client:
            print(f"⚠️ AWS Bedrock client unavailable for translation to {target_lang_name}")
            return text + f" (Translation to {target_lang_name} unavailable)"
            
        prompt_map = {
            '2': f"Translate the following English text to Hindi. Only provide the translated text. Do not include any conversational filler. Text: '{text}'",
            '3': f"Translate the following English text to Telugu. Only provide the translated text. Do not include any conversational filler. Text: '{text}'"
        }
        
        prompt_text = prompt_map.get(target_lang_key)
        if not prompt_text: return text

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{"role": "user", "content": prompt_text}],
            "max_tokens": 500, "temperature": 0.1,
        })
        
        response = bedrock_client.invoke_model(
            body=body, 
            modelId=CLAUDE_MODEL_ID, 
            accept="application/json", 
            contentType="application/json"
        )
        
        response_body = json.loads(response.get('body').read())
        translated_text = response_body.get('content', [{'text': ''}])[0].get('text', '').strip()
        
        print(f"Translated to {target_lang_name}: {translated_text}")
        return translated_text
        
    except Exception as e:
        print(f"❌ Error during translation to {target_lang_name}: {e}")
        
        # Fallback responses based on language
        fallbacks = {
            '2': "मुझे खेद है, मैं अभी हिंदी में सेवा प्रदान करने में असमर्थ हूं। कृपया अंग्रेजी में बात करें।",
            '3': "క్షమించండి, నేను ప్రస్తుతం తెలుగులో సేవలను అందించలేను. దయచేసి ఆంగ్లంలో మాట్లాడండి."
        }
        
        return fallbacks.get(target_lang_key, "I am currently unable to provide service in the selected language.")

def update_call_status_and_outcome(task_id, status, outcome):
    """
    Updates the call status and outcome in memory and database.
    """
    if task_id in call_tasks:
        call_tasks[task_id].update({
            'status': status,
            'call_outcome_notes': outcome,
            'timestamp': datetime.now().isoformat()
        })
        print(f"✅ Task {task_id} updated to '{status}' with outcome: {outcome}.")

        # Update the status in the database as well
        update_task_status_in_db(task_id, status)
        
        # Record the call outcome in the database
        record_call_outcome(task_id, outcome)
    else:
        print(f"⚠️ Task ID {task_id} not found for status update.")

def create_task_router_task(task_id, task_details, outcome, call_sid):
    """
    Creates a task in Twilio TaskRouter for agent handoff.
    """
    attributes = {
        "customer_name": task_details.get('customer_name'),
        "customer_id": task_details.get('customer_id'),
        "phone_number": task_details.get('customer_phone_number'),
        "loan_id": task_details.get('loan_id_full'),
        "call_sid": call_sid,
        "summary": f"Outbound call regarding EMI for loan {task_details.get('loan_last4')}. Outcome: {outcome}.",
        "type": "voice_handoff"
    }
    try:
        task = twilio_client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
            .tasks \
            .create(
                attributes=json.dumps(attributes),
                workflow_sid=TWILIO_TASK_ROUTER_WORKFLOW_SID,
                priority=10,
                timeout=3600
            )
        print(f"✅ TaskRouter task created: {task.sid}")
    except Exception as e:
        print(f"❌ Failed to create TaskRouter task: {e}")

# --- TwiML Decorator for Task Validation ---
def require_task(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        task_id = request.values.get('task_id')
        print(f"-> Executing {f.__name__} for Task ID: {task_id}")

        if not task_id or task_id not in call_tasks:
            response = VoiceResponse()
            # Enhanced error message with more specific information and guidance
            response.say(
                "I apologize, but I'm unable to retrieve your call information at the moment. "
                "This may be due to a recent system update or connectivity issue. "
                "Please try again later or contact our customer support at 1800-123-4567 for immediate assistance. "
                "Thank you for your patience.",
                voice="Polly.Raveena", 
                language="en-IN"
            )
            response.hangup()
            return str(response)
        
        task_details = call_tasks[task_id]
        lang_code_key = task_details.get('current_language', '1')
        lang_info = LANG_CONFIG.get(lang_code_key, LANG_CONFIG['1'])
        
        return f(task_id, task_details, lang_info, *args, **kwargs)
    return decorated_function

# --- TwiML Routes for Outbound Voice System ---
@app.route("/voice-language-select", methods=['POST', 'GET'])
@require_task
def voice_language_select(task_id, task_details, lang_info):
    response = VoiceResponse()
    action_url = f'{NGROK_URL}/voice-language-select-handler?task_id={task_id}'
    gather = Gather(num_digits=1, action=action_url, method='POST')
    gather.say("Hello. Welcome to our financial assistance line. For English, press 1.", voice="Polly.Raveena", language="en-IN")
    gather.say("हिंदी के लिए, 2 दबाएं।", voice="Polly.Raveena", language="hi-IN")
    gather.say("తెలుగు కోసం, 3 నొక్కండి.", voice="Polly.Raveena", language="te-IN")
    response.append(gather)
    response.say("We did not receive your input. Goodbye.", voice="Polly.Raveena", language="en-IN")
    response.hangup()
    return str(response)

@app.route("/voice-language-select-handler", methods=['POST'])
@require_task
def voice_language_select_handler(task_id, task_details, lang_info):
    digit_pressed = request.form.get('Digits')
    response = VoiceResponse()
    if digit_pressed in LANG_CONFIG:
        call_tasks[task_id]['current_language'] = digit_pressed
        print(f"Language for Task {task_id} set to: {LANG_CONFIG[digit_pressed]['name']}")
        response.redirect(f'{NGROK_URL}/voice-confirm-identity?task_id={task_id}')
    else:
        response.say("Invalid selection. Goodbye.", voice="Polly.Raveena", language="en-IN")
        response.hangup()
    return str(response)

@app.route("/voice-confirm-identity", methods=['POST', 'GET'])
@require_task
def voice_confirm_identity(task_id, task_details, lang_info):
    response = VoiceResponse()
    customer_name = task_details.get("customer_name", "Valued Customer")
    prompt_english = f"Hello, this is south india finvest Bank AI Assistant calling. Am I speaking with {customer_name}?"
    prompt_translated = translate_text(prompt_english, task_details['current_language'], lang_info['name'])
    
    action_url = f"{NGROK_URL}/voice-handle-identity-confirmation?task_id={task_id}"
    gather = Gather(input="speech", timeout="5", action=action_url, method="POST")
    gather.say(prompt_translated, voice=lang_info['voice'], language=lang_info['code'])
    response.append(gather)
    
    # Enhanced fallback message
    fallback_prompt = translate_text(
        "I'm sorry, I didn't hear your response. This call is regarding your loan account. "
        "If this is a convenient time to talk, please say 'yes'. Otherwise, we'll try to reach you later.", 
        task_details['current_language'], lang_info['name']
    )
    response.say(fallback_prompt, voice=lang_info['voice'], language=lang_info['code'])
    response.hangup()
    return str(response)

@app.route("/voice-handle-identity-confirmation", methods=['POST'])
@require_task
def voice_handle_identity_confirmation(task_id, task_details, lang_info):
    speech_result = request.form.get('SpeechResult', '').lower()
    response = VoiceResponse()
    
    affirmative = ["yes", "yeah", "ok", "haan", "ha", "sari", "avunu", "hūdu"]
    negative = ["no", "nope", "nahi", "nahee", "ledu", "illa"]

    if any(keyword in speech_result for keyword in affirmative):
        response.redirect(f'{NGROK_URL}/voice-emi-details?task_id={task_id}')
    elif any(keyword in speech_result for keyword in negative):
        prompt = translate_text("I understand. For security, I cannot proceed. Goodbye.", task_details['current_language'], lang_info['name'])
        response.say(prompt, voice=lang_info['voice'], language=lang_info['code'])
        update_call_status_and_outcome(task_id, 'completed', 'Identity_Not_Confirmed')
        response.hangup()
    else:
        prompt = translate_text("I didn't understand. Please say 'yes' or 'no'.", task_details['current_language'], lang_info['name'])
        response.say(prompt, voice=lang_info['voice'], language=lang_info['code'])
        response.redirect(f'{NGROK_URL}/voice-confirm-identity?task_id={task_id}')
    return str(response)

@app.route("/voice-emi-details", methods=['POST', 'GET'])
@require_task
def voice_emi_details(task_id, task_details, lang_info):
    response = VoiceResponse()

    prompt1_english = f"Thank you. I'm calling about your loan ending in {task_details.get('loan_last4', 'XXXX')}, which has an outstanding EMI of {task_details.get('emi_amount', 'a certain amount')} due on {task_details.get('due_date', 'a recent date')}."
    prompt2_english = "I understand payments can be delayed — I'm here to help you avoid any further impact."

    response.say(translate_text(prompt1_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    response.pause(length=1)
    response.say(translate_text(prompt2_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    response.redirect(f'{NGROK_URL}/voice-explain-impact?task_id={task_id}')
    return str(response)

@app.route("/voice-explain-impact", methods=['POST', 'GET'])
@require_task
def voice_explain_impact(task_id, task_details, lang_info):
    response = VoiceResponse()

    prompt1_english = "Please note: if this EMI remains unpaid, it may be reported to the credit bureau, which can affect your credit score."
    prompt2_english = "Continued delay may also classify your account as delinquent, leading to penalty charges or collection notices."

    response.say(translate_text(prompt1_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    response.pause(length=2)
    response.say(translate_text(prompt2_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    response.redirect(f'{NGROK_URL}/voice-offer-support?task_id={task_id}')
    return str(response)

@app.route("/voice-offer-support", methods=['POST', 'GET'])
@require_task
def voice_offer_support(task_id, task_details, lang_info):
    response = VoiceResponse()

    prompt1_english = "If you're facing difficulties, we have options like part payments or revised EMI plans. Would you like me to connect to one of our agent,to assist you better ?"
    
    response.say(translate_text(prompt1_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    confirm_assistance_english = ""
    confirm_assistance_translated = translate_text(confirm_assistance_english, task_details['current_language'], lang_info['name'])

    action_url = f"{NGROK_URL}/voice-handle-support-choice?task_id={task_id}"
    gather = Gather(input="speech", timeout="7", action=action_url, method="POST")
    gather.say(confirm_assistance_translated, voice=lang_info['voice'], language=lang_info['code'])
    response.append(gather)
    
    fallback_prompt = translate_text("We did not receive a clear response. Goodbye.", task_details['current_language'], lang_info['name'])
    response.say(fallback_prompt, voice=lang_info['voice'], language=lang_info['code'])
    response.hangup()
    return str(response)

@app.route("/voice-handle-support-choice", methods=['POST'])
@require_task
def voice_handle_support_choice(task_id, task_details, lang_info):
    speech_result = request.form.get('SpeechResult', '').lower()
    response = VoiceResponse()

    affirmative_keywords = ["yes", "yeah", "ok", "yep", "haan", "ha", "sari", "sare", "avunu", "hūdu", "pay", "payment", "help", "agent", "support"]
    
    if any(keyword in speech_result for keyword in affirmative_keywords):
        response.redirect(f'{NGROK_URL}/voice-connect-to-agent?task_id={task_id}&outcome=Customer_Agreed_Assistance')
    else:
        prompt = translate_text("I understand. If you change your mind, please call us back. Thank you. Goodbye.", task_details['current_language'], lang_info['name'])
        response.say(prompt, voice=lang_info['voice'], language=lang_info['code'])
        update_call_status_and_outcome(task_id, 'completed', 'No_Agreement_For_Assistance')
        response.hangup()
    return str(response)

@app.route("/voice-connect-to-agent", methods=['POST', 'GET'])
@require_task
def voice_connect_to_agent(task_id, task_details, lang_info):
    response = VoiceResponse()

    call_outcome_notes = request.args.get('outcome', 'Agent_Requested')
    update_call_status_and_outcome(task_id, 'agent_handoff', call_outcome_notes)
    
    send_whatsapp_summary(
        task_id, task_details['customer_phone_number'], task_details['customer_name'],
        task_details['loan_id_full'], task_details['emi_amount'], call_outcome_notes
    )

    create_task_router_task(task_id, task_details, call_outcome_notes, request.values.get('CallSid'))

    prompt = translate_text("Please wait while I connect you to an agent.", task_details['current_language'], lang_info['name'])
    response.say(prompt, voice=lang_info['voice'], language=lang_info['code'])
    response.dial(AGENT_PHONE_NUMBER)
    
    return str(response)

# --- Outbound Campaign Management Routes ---
@app.route("/trigger-call", methods=['POST'])
def trigger_call():
    """
    Endpoint to manually trigger a call for a specific customer.
    """
    data = request.json
    to_number = data.get('to_number')
    customer_id = data.get('customer_id')
    loan_id = data.get('loan_id_full')
    
    if not to_number or not customer_id or not loan_id:
        return jsonify({"error": "Missing required fields: to_number, customer_id, or loan_id_full"}), 400

    try:
        # Create a new collection task in the database
        db = DatabaseSession()
        query = text("""
            INSERT INTO CollectionTask 
            (customer_id, loan_id, scheduled_for, priority_level, assigned_to, status)
            VALUES 
            (:customer_id, :loan_id, NOW(), 1, 'AI_BOT', 'pending')
            RETURNING task_id
        """)
        
        result = db.execute(query, {
            'customer_id': customer_id,
            'loan_id': loan_id
        })
        task_id = str(result.fetchone()[0])
        db.commit()
        
        # Fetch complete customer details to populate call_tasks
        details_query = text("""
            SELECT 
                c.full_name AS customer_name, 
                c.phone_number AS customer_phone_number,
                l.loan_id AS loan_id_full,
                RIGHT(l.loan_id, 4) AS loan_last4,
                e.amount_due AS emi_amount,
                TO_CHAR(e.due_date, 'DD Month') AS due_date
            FROM 
                Customer c
            JOIN 
                Loan l ON c.customer_id = l.customer_id
            LEFT JOIN 
                EMI e ON l.loan_id = e.loan_id AND e.status = 'pending'
            WHERE 
                c.customer_id = :customer_id AND l.loan_id = :loan_id
            LIMIT 1
        """)
        
        details_result = db.execute(details_query, {
            'customer_id': customer_id,
            'loan_id': loan_id
        })
        
        customer_details = details_result.fetchone()
        db.close()
        
        if customer_details:
            call_tasks[task_id] = {
                'status': 'pending',
                'customer_id': customer_id,
                'customer_name': customer_details.customer_name,
                'customer_phone_number': to_number,
                'loan_id_full': customer_details.loan_id_full,
                'loan_last4': customer_details.loan_last4,
                'emi_amount': f'₹{customer_details.emi_amount:,.0f}' if customer_details.emi_amount else '₹0',
                'due_date': customer_details.due_date,
                'current_language': '1'
            }
        else:
            # Fall back to manually provided data if database doesn't return details
            call_tasks[task_id] = {
                'status': 'pending',
                'customer_id': customer_id,
                'customer_name': data.get('customer_name', 'Valued Customer'),
                'customer_phone_number': to_number,
                'loan_id_full': loan_id,
                'loan_last4': loan_id[-4:] if loan_id else 'XXXX',
                'emi_amount': data.get('emi_amount', '₹0'),
                'due_date': data.get('due_date', 'upcoming'),
                'current_language': '1'
            }
        
        return jsonify({
            "message": "Task created in database. Use /start-campaign to initiate calls.", 
            "task_id": task_id
        }), 200
        
    except Exception as e:
        print(f"❌ Error creating task: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/start-campaign", methods=['GET'])
def start_campaign():
    """
    Initiates outbound calls for high-risk customers.
    """
    try:
        # Clear the existing call_tasks to start fresh
        call_tasks.clear()
        
        # Check if we should reset tasks (defaults to False)
        reset_tasks = request.args.get('reset', 'false').lower() == 'true'
        
        if reset_tasks:
            # Reset in-progress tasks to pending
            db = DatabaseSession()
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE status IN ('in-progress', 'failed')
                RETURNING task_id
            """)
            reset_result = db.execute(reset_query)
            reset_tasks_list = [str(row[0]) for row in reset_result]
            db.commit()
            db.close()
            print(f"🔄 Reset {len(reset_tasks_list)} tasks to 'pending' status")
        else:
            reset_tasks_list = []
        
        # Fetch high-risk customers from the database
        customers_to_call = fetch_high_risk_customers()
        
        # Populate the call_tasks dictionary for easy lookup
        for customer in customers_to_call:
            task_id = customer['task_id']
            call_tasks[task_id] = customer
        
        print(f"📞 Found {len(customers_to_call)} customers to call.")

        calls_initiated_details = []
        for customer in customers_to_call:
            task_id = customer['task_id']
            customer_phone_number = customer['customer_phone_number']
            call_tasks[task_id]['status'] = 'dialing'
            
            # Update the task status in the database
            update_task_status_in_db(task_id, 'in-progress')

            call = twilio_client.calls.create(
                url=f'{NGROK_URL}/voice-language-select?task_id={task_id}',
                to=customer_phone_number,
                from_=TWILIO_PHONE_NUMBER
            )
            calls_initiated_details.append({
                'task_id': task_id,
                'customer_name': customer['customer_name'],
                'phone_number': customer_phone_number,
                'call_sid': call.sid
            })
            print(f"✅ Call initiated to {customer_phone_number} for Task ID {task_id}. Call SID: {call.sid}")

        return jsonify({
            "message": f"Campaign started. Calls initiated for {len(customers_to_call)} customers.",
            "calls_initiated": calls_initiated_details,
            "tasks_reset": reset_tasks_list
        }), 200
    except Exception as e:
        print(f"Error starting campaign: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/reset-tasks", methods=['POST'])
def reset_tasks():
    """
    Resets tasks to 'pending' status so they can be called again.
    """
    try:
        task_ids = []
        content_type = request.headers.get('Content-Type', '')
        
        # Handle different content types appropriately
        if 'application/json' in content_type and request.data:
            try:
                data = request.json or {}
                task_ids = data.get('task_ids', [])
            except:
                print("Warning: Could not parse JSON data")
        
        db = DatabaseSession()
        
        if task_ids:
            # Reset specific tasks
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE task_id IN :task_ids
                RETURNING task_id
            """)
            reset_result = db.execute(reset_query, {'task_ids': tuple(task_ids)})
        else:
            # Reset all non-pending tasks
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE status IN ('in-progress', 'failed', 'completed', 'agent_handoff')
                RETURNING task_id
            """)
            reset_result = db.execute(reset_query)
        
        reset_tasks_list = [str(row[0]) for row in reset_result]
        db.commit()
        db.close()
        
        print(f"🔄 Reset {len(reset_tasks_list)} tasks to 'pending' status")
        return jsonify({
            "message": f"Successfully reset {len(reset_tasks_list)} tasks to 'pending' status",
            "reset_task_ids": reset_tasks_list
        }), 200
        
    except Exception as e:
        print(f"❌ Error resetting tasks: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/customers", methods=['GET'])
def get_customers():
    """
    Returns the list of customers with collection tasks for the frontend.
    """
    try:
        db = DatabaseSession()
        
        query = text("""
            WITH unique_loans AS (
                -- Get one row per loan-customer combination
                SELECT DISTINCT ON (l.loan_id, c.customer_id)
                    ct.task_id,
                    c.customer_id,
                    c.full_name AS customer_name,
                    c.phone_number AS customer_phone_number,
                    l.loan_id AS loan_id_full,
                    RIGHT(l.loan_id, 4) AS loan_last4,
                    COALESCE(
                        (
                            SELECT amount_due 
                            FROM emi 
                            WHERE loan_id = l.loan_id 
                            AND status = 'pending' 
                            ORDER BY due_date ASC 
                            LIMIT 1
                        ), 0
                    ) AS emi_amount,
                    COALESCE(
                        (
                            SELECT TO_CHAR(due_date, 'DD Month')
                            FROM emi
                            WHERE loan_id = l.loan_id
                            AND status = 'pending'
                            ORDER BY due_date ASC
                            LIMIT 1
                        ), 'Unknown'
                    ) AS due_date,
                    ct.status,
                    ct.priority_level,
                    COALESCE(rs.risk_segment, 'Medium') AS risk_segment
                FROM
                    loan l
                JOIN
                    collectiontask ct ON l.loan_id = ct.loan_id
                JOIN
                    customer c ON ct.customer_id = c.customer_id
                LEFT JOIN
                    (
                        SELECT customer_id, risk_segment, score,
                               ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY risk_date DESC) as rn
                        FROM riskscore
                    ) rs ON c.customer_id = rs.customer_id AND rs.rn = 1
                ORDER BY
                    l.loan_id, c.customer_id, ct.created_at DESC
            )
            SELECT * FROM unique_loans
            ORDER BY
                CASE 
                    WHEN status = 'pending' THEN 1
                    WHEN status = 'in-progress' THEN 2
                    ELSE 3
                END,
                priority_level DESC,
                risk_segment DESC NULLS LAST
            LIMIT 100;
        """)
        
        result = db.execute(query)
        customer_list = []
        
        for row in result:
            try:
                emi_amount = f'₹{float(row.emi_amount):,.0f}' if row.emi_amount else 'Unknown'
            except (TypeError, ValueError):
                emi_amount = 'Unknown'
                
            customer_list.append({
                'task_id': str(row.task_id),
                'customer_id': row.customer_id,
                'customer_name': row.customer_name or 'Unknown',
                'customer_phone_number': row.customer_phone_number or 'Unknown',
                'loan_id_full': row.loan_id_full or 'Unknown',
                'loan_last4': row.loan_last4 or 'Unknown',
                'emi_amount': emi_amount,
                'due_date': row.due_date or 'Unknown',
                'status': row.status or 'pending',
                'risk_segment': row.risk_segment or 'Medium',
                'priority_level': row.priority_level or 1
            })
        
        db.close()
        print(f"API returned {len(customer_list)} unique customer-loan combinations")
        
        return jsonify(customer_list), 200
    except Exception as e:
        print(f"Error fetching customers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/webhook/whatsapp", methods=['POST'])
def outbound_whatsapp_webhook():
    """
    Webhook for handling incoming WhatsApp messages for the outbound system.
    """
    try:
        # Get incoming message details
        incoming_msg = request.form.get('Body', '').strip().lower()
        sender_phone = request.form.get('From', '')  # Format: 'whatsapp:+1234567890'
        sender_phone = sender_phone.replace('whatsapp:', '')  # Clean the phone number
        
        # Log the incoming message
        print(f"📱 Received WhatsApp message from {sender_phone}: '{incoming_msg}'")
        
        # Create a response
        resp = MessagingResponse()
        
        # Try to identify customer by phone number
        db = DatabaseSession()
        query = text("""
            SELECT 
                c.customer_id, 
                c.full_name, 
                l.loan_id,
                e.amount_due,
                e.due_date
            FROM 
                customer c
            JOIN 
                loan l ON c.customer_id = l.customer_id
            LEFT JOIN 
                emi e ON l.loan_id = e.loan_id AND e.status = 'pending'
            WHERE 
                c.phone_number LIKE '%' || :phone || '%'
            ORDER BY 
                e.due_date ASC
            LIMIT 1
        """)
        
        result = db.execute(query, {
            'phone': sender_phone[-10:]  # Use last 10 digits to match various formats
        })
        
        customer_info = result.fetchone()
        db.close()
        
        if customer_info:
            # Format the EMI date and amount
            due_date_str = "upcoming"
            if customer_info.due_date:
                due_date_str = customer_info.due_date.strftime('%d %B')
            
            emi_amount_str = "to be confirmed"
            if customer_info.amount_due:
                emi_amount_str = f"₹{customer_info.amount_due:,.0f}"
            
            # Create response message with your requested format
            message = (
                f"📱 South India Finvest Bank Payment Reminder 📱\n\n"
                f"Hello {customer_info.full_name},\n\n"
                f"This is regarding your loan {customer_info.loan_id}.\n"
                f"EMI Amount Due: {emi_amount_str}\n"
                f"Due Date: {due_date_str}\n\n"
                f"To make your payment online, please visit: https://southindiafinvest.com/payment/\n"
                f"For assistance, call our support at 1800-123-4567\n\n"
                f"Thank you for banking with us."
            )
            resp.message(message)
        else:
            # No customer found
            resp.message(
                "📱 South India Finvest Bank\n\n"
                "We couldn't find your account information based on this phone number.\n\n"
                "For assistance, please contact our customer service at 1800-123-4567.\n\n"
                "Thank you for banking with us."
            )
        
        return str(resp)
    
    except Exception as e:
        print(f"❌ Error processing WhatsApp webhook: {e}")
        # Return a basic response even if there's an error
        resp = MessagingResponse()
        resp.message(
            "📱 South India Finvest Bank\n\n"
            "We're experiencing technical difficulties. Please try again later or call our customer service at 1800-123-4567.\n\n"
            "Thank you for your patience."
        )
        return str(resp)

def send_whatsapp_summary(task_id, to_number, customer_name, loan_id, emi_amount, outcome):
    """
    Sends a summary of the call outcome to the customer via WhatsApp.
    """
    if not to_number:
        print("⚠️ Customer phone number not available. Skipping WhatsApp summary.")
        return
    
    # Clean and format the phone number by removing all spaces
    formatted_number = to_number.replace(" ", "")
    
    print(f"Original phone number: '{to_number}'")
    print(f"Formatted phone number: '{formatted_number}'")
    
    summary_message = (
        f"📱 South India Finvest Bank Payment Reminder 📱\n\n"
        f"Hello {customer_name},\n\n"
        f"This is a follow-up to our recent call about your loan {loan_id}.\n"
        f"EMI Amount Due: {emi_amount}\n\n"
        f"To make your payment online, please visit: https://southindiafinvest.com/payment/\n"
        f"For assistance, call our support at 1800-123-4567\n\n"
        f"Thank you for banking with us."
    )
    try:
        message = twilio_client.messages.create(
            from_=f'whatsapp:{TWILIO_PHONE_NUMBER}',
            body=summary_message,
            to=f'whatsapp:{formatted_number}'
        )
        print(f"✅ WhatsApp summary sent to customer at {formatted_number}. SID: {message.sid}")
    except Exception as e:
        print(f"❌ Failed to send WhatsApp summary to customer: {e}")
@app.route("/api/debug", methods=['GET'])
def debug_info():
    """
    Returns debug information about the database.
    """
    try:
        db = DatabaseSession()
        
        # Get counts
        counts_query = text("""
            SELECT 
                (SELECT COUNT(*) FROM customer) as customers,
                (SELECT COUNT(*) FROM loan) as loans,
                (SELECT COUNT(*) FROM collectiontask) as tasks,
                (SELECT COUNT(DISTINCT loan_id) FROM collectiontask) as unique_loan_tasks,
                (SELECT COUNT(DISTINCT customer_id) FROM customer) as unique_customers
        """)
        
        counts = db.execute(counts_query).fetchone()
        
        # Get sample data
        sample_query = text("""
            SELECT 
                ct.task_id, 
                ct.customer_id, 
                ct.loan_id,
                ct.status,
                ct.created_at
            FROM 
                collectiontask ct
            ORDER BY 
                ct.created_at DESC
            LIMIT 10
        """)
        
        sample_rows = db.execute(sample_query).fetchall()
        sample_data = []
        
        for row in sample_rows:
            sample_data.append({
                'task_id': str(row.task_id),
                'customer_id': row.customer_id,
                'loan_id': row.loan_id,
                'status': row.status,
                'created_at': str(row.created_at)
            })
        
        db.close()
        
        return jsonify({
            "counts": {
                "customers": counts.customers,
                "loans": counts.loans, 
                "tasks": counts.tasks,
                "unique_loan_tasks": counts.unique_loan_tasks,
                "unique_customers": counts.unique_customers
            },
            "sample_tasks": sample_data
        }), 200
    except Exception as e:
        print(f"Error getting debug info: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/debug-templates')
def debug_templates():
    """Temporary route to debug template directory configuration"""
    import os
    
    template_dir = app.template_folder
    templates = []
    
    try:
        templates = os.listdir(template_dir)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "template_folder": template_dir,
            "current_directory": os.getcwd()
        })
    
       
    
    return jsonify({
        "template_folder": template_dir,
        "templates_found": templates,
        "current_directory": os.getcwd()
    })

# --- Outbound Campaign Route ---
@app.route('/outbound-campaign', methods=['GET'])
def outbound_campaign():
    """
    Dedicated route to serve the outbound calling campaign interface.
    """
    try:
        return render_template('outbound.html')
    except Exception as e:
        logging.error(f"Error serving outbound.html: {e}")
        return f"""
        <h1>Error loading outbound campaign system</h1>
        <p>Details: {str(e)}</p>
        <p>Make sure the file exists at: {os.path.join(app.template_folder, 'outbound.html')}</p>
        """, 500

def get_bedrock_client():
    """
    Creates and returns an AWS Bedrock client
    """
    try:
        config = Config(
            retries={"max_attempts": 3, "mode": "standard"},
            region_name="us-east-1"  # Use your AWS region
        )
        
        # Create the Bedrock Runtime client
        bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            config=config
        )
        
        return bedrock_client
    except Exception as e:
        print(f"❌ Error creating Bedrock client: {e}")
        return None

# Initialize the Bedrock client
bedrock_client = get_bedrock_client()

# Define the Claude model ID
CLAUDE_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
if __name__ == "__main__":
    # Start the Flask app using socketio.run
    socketio.run(app, debug=True, host='0.0.0.0', port=5504)