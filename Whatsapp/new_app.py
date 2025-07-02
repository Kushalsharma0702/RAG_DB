from flask import Flask, request, jsonify, session, send_from_directory
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime, timezone # Import timezone for timezone-aware datetimes
from flask_cors import CORS
import logging # Ensure logging is explicitly imported here
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException # Added import for TwilioRestException

# Import from new_ files to ensure consistency with recent fixes
from new_config import (
    SECRET_KEY, FLASK_DEBUG,
    TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID, TWILIO_PHONE,
    TWILIO_TASK_ROUTER_WORKSPACE_SID, TWILIO_TASK_ROUTER_WORKFLOW_SID
)
from new_otp_manager import send_otp, validate_otp
from new_bedrock_client import generate_response as bedrock_generate_response, get_chat_summary, get_embedding, get_intent_from_text as bedrock_get_intent_from_text, generate_data_response
from new_intent_classifier import classify_intent # Keep direct import for rule-based
from new_database import (
    fetch_customer_by_account_id, # Corrected function name
    get_or_create_customer_by_phone, # Added for WhatsApp flow
    save_chat_interaction,
    save_unresolved_chat,
    get_last_three_chats,
    create_tables, # Will be called after migration to ensure tables exist with correct schema
    Session,
    ChatInteraction,
    RAGDocument, # Explicitly import RAGDocument for agent dashboard/task update
    Customer,    # Explicitly import Customer for agent dashboard/customer lookup
    get_all_chats_for_session, # For fetching full session history
    get_customer_by_id, # For fetching customer details by ID
    update_rag_document_status # For updating RAGDocument status
)
from new_rag_utils import fetch_data
# from new_db_migration import run_migration # This will now be called first to drop tables
from new_twilio_chat import create_conversation, send_message_to_conversation, create_task_for_handoff, add_whatsapp_participant_to_conversation, add_customer_participant_to_conversation
from flask import Flask, request, jsonify, session, send_from_directory
# ... (other imports remain unchanged)
from new_twilio_chat import create_task_for_handoff
load_dotenv()

# Configure logging (important to do this very early, before app creation)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__, static_folder='frontend')
app.secret_key = SECRET_KEY # Use SECRET_KEY from new_config
app.debug = FLASK_DEBUG == 'True' # Use FLASK_DEBUG from new_config
CORS(app) # Enable CORS

twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# Initialize database and run migrations on app startup
with app.app_context():
    try:
        # First, run migration to drop and clean up tables
        # run_migration()
        # Then, create tables (they will be created with the correct schema from models)
        create_tables()
        logging.info("Database setup completed successfully")
    except Exception as e:
        logging.error(f"Warning: Database migration or table creation encountered an issue: {e}")
        logging.error("The application will continue, but some features may not work correctly.")


# --- Frontend Serving Routes ---
@app.route('/')
def index():
    if 'chat_session_id' not in session:
        session['chat_session_id'] = str(uuid.uuid4())
        session['auth_state'] = 0 # 0: unauthenticated, 1: awaiting_account_id, 2: awaiting_otp, 3: authenticated
        session['customer_id'] = None
        session['phone_number'] = None
        session['original_query_intent'] = None # Store the intent from menu selection
        session['next_expected_input'] = 'menu_choice' # Initial state for web chat
    return send_from_directory(app.static_folder, 'new_index.html')

@app.route('/agent_dashboard')
def agent_dashboard():
    return send_from_directory(app.static_folder, 'new_agent_dashboard.html')

@app.route('/agent_chat_interface')
def agent_chat_interface():
    return send_from_directory(app.static_folder, 'new_agent_chat_interface.html')


# --- Internal Helper Functions for OTP ---
def _send_otp_internal(phone_number: str, customer_id: str) -> tuple[bool, str]:
    """Internal function to send OTP and store its state for validation."""
    otp = send_otp(phone_number)
    if otp:
        logging.info(f"OTP sent to {phone_number} for customer {customer_id}.")
        return True, "OTP sent successfully!"
    logging.error(f"Failed to send OTP to {phone_number}.")
    return False, "Failed to send OTP."

def _validate_otp_internal(phone_number: str, user_otp: str) -> tuple[bool, str]:
    """Internal function to validate OTP."""
    is_valid, message = validate_otp(phone_number, user_otp)
    return is_valid, message

def _process_account_query(intent: str, customer_id: str) -> str:
    """Helper function to fetch and format account-specific data."""
    data = fetch_data(intent, customer_id)
    if data:
        return generate_data_response(intent, data) # Uses new_bedrock_client.generate_data_response
    else:
        return "I couldn't find details for your request. This might be because the data is unavailable or your account is not linked."


# --- Web Chat Endpoint ---
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get('message', '').strip()
    
    if not user_message:
        return jsonify({"response": "Please provide a message.", "auth_state": session.get('auth_state', 0)})

    # Initialize/restore session state
    chat_session_id = session.get('chat_session_id')
    if not chat_session_id:
        session['chat_session_id'] = str(uuid.uuid4())
        session['auth_state'] = 0
        session['customer_id'] = None
        session['phone_number'] = None
        session['original_query_intent'] = None
        session['next_expected_input'] = 'menu_choice' # Initial state for web chat
        logging.info(f"New web chat session initialized: {session['chat_session_id']}")
        # For a new session, immediately present the menu
        bot_response = "Welcome! How can I help you today?\n1. My EMI\n2. Account Balance\n3. Loan Details\n\nPlease reply with the number of your choice."
        save_chat_interaction(session_id=session['chat_session_id'], customer_id=session['customer_id'], message_text=user_message, sender='user', intent='greeting')
        save_chat_interaction(session_id=session['chat_session_id'], customer_id=session['customer_id'], message_text=bot_response, sender='bot', intent='menu_presentation')
        return jsonify({"response": bot_response, "auth_state": session['auth_state'], "action_needed": "awaiting_menu_choice"})

    auth_state = session.get('auth_state', 0)
    customer_id = session.get('customer_id')
    phone_number = session.get('phone_number')
    original_query_intent = session.get('original_query_intent')
    next_expected_input = session.get('next_expected_input')

    bot_response = ""
    action_needed = ""

    try:
        # Save user message
        save_chat_interaction(
            session_id=chat_session_id,
            customer_id=customer_id,
            message_text=user_message,
            sender='user',
            intent='in_chat_flow'
        )

        # Agent escalation always possible if authenticated
        if classify_intent(user_message) == "agent_escalation" or user_message.lower() in ["speak to agent", "connect me to agent", "human help"]:
            if auth_state == 3: # Only escalate if customer is identified
                chat_history_for_summary = get_all_chats_for_session(customer_id, chat_session_id)
                summary_text = get_chat_summary(chat_history_for_summary)
                summary_embedding = get_embedding(summary_text)

                conv_sid = create_conversation(customer_id)
                if conv_sid:
                    add_customer_participant_to_conversation(conv_sid, customer_id)
                else:
                    logging.error("Failed to create/get conversation for web chat escalation.")
                    bot_response = "I'm sorry, I couldn't initiate the agent transfer. Please try again later."
                    action_needed = "none"

                task_sid = create_task_for_handoff(
                    customer_id=customer_id,
                    session_id=chat_session_id,
                    summary=summary_text,
                    customer_phone_number=phone_number,
                    conversation_sid=conv_sid,
                    channel_type="web"
                )
                
                if task_sid:
                    save_unresolved_chat(customer_id=customer_id, summary=summary_text, embedding=summary_embedding, task_sid=task_sid, status='pending')
                    with Session() as db_session:
                        db_session.query(ChatInteraction).filter(
                            ChatInteraction.session_id == chat_session_id,
                            ChatInteraction.customer_id == customer_id
                        ).update({"is_escalated": True, "conversation_sid": conv_sid}, synchronize_session=False)
                        db_session.commit()
                        logging.info(f"Web chat: Marked all chat interactions for session {chat_session_id} as escalated.")

                    bot_response = "I've escalated your query to a human agent. Please wait while an agent reviews your request. You will be connected shortly."
                    action_needed = "escalated" # This special action_needed tells frontend to show agent message
                    # Reset session state after escalation
                    session['auth_state'] = 0
                    session['customer_id'] = None
                    session['phone_number'] = None
                    session['original_query_intent'] = None
                    session['next_expected_input'] = 'menu_choice' # Reset to initial state
                else:
                    bot_response = "I'm sorry, I couldn't connect you to an agent right now. Please try again later."
                    action_needed = "none"
            else:
                bot_response = "Please provide your Account ID and verify your identity before I can connect you to an agent for security reasons."
                action_needed = "awaiting_account_id"
                session['auth_state'] = 1
                session['original_query_intent'] = "agent_escalation_request"
                session['next_expected_input'] = 'account_id'

            save_chat_interaction(session_id=chat_session_id, customer_id=customer_id, message_text=bot_response, sender='bot', intent='agent_escalation_response')
            return jsonify({"response": bot_response, "auth_state": session['auth_state'], "action_needed": action_needed})

        # --- Menu-driven Authentication Flow Logic (Web & WhatsApp) ---
        if auth_state == 0 and next_expected_input == 'menu_choice':
            if user_message == '1':
                original_query_intent = 'emi'
                bot_response = "Great! Please provide your Account ID to retrieve your EMI details."
                action_needed = "awaiting_account_id"
                auth_state = 1
            elif user_message == '2':
                original_query_intent = 'balance'
                bot_response = "Great! Please provide your Account ID to retrieve your Account Balance."
                action_needed = "awaiting_account_id"
                auth_state = 1
            elif user_message == '3':
                original_query_intent = 'loan'
                bot_response = "Great! Please provide your Account ID to retrieve your Loan Details."
                action_needed = "awaiting_account_id"
                auth_state = 1
            else:
                bot_response = "I didn't understand that. Please select 1, 2, or 3 from the menu."
                action_needed = "awaiting_menu_choice" # Remain in menu choice state
        
            session['auth_state'] = auth_state
            session['original_query_intent'] = original_query_intent
            session['next_expected_input'] = action_needed # Update next expected input state

        elif auth_state == 1 and next_expected_input == 'account_id':
            account_id = user_message
            customer_info = fetch_customer_by_account_id(account_id)
            
            if customer_info:
                session['customer_id'] = customer_info['customer_id']
                session['phone_number'] = customer_info['phone_number']
                
                otp_sent, otp_message = _send_otp_internal(customer_info['phone_number'], customer_info['customer_id'])
                if otp_sent:
                    bot_response = f"{otp_message} Please enter the 6-digit OTP sent to your registered number."
                    action_needed = "awaiting_otp"
                    auth_state = 2
                else:
                    bot_response = f"Failed to send OTP: {otp_message}. Please check your account ID or try again."
                    action_needed = "awaiting_account_id" # Remain in this state
            else:
                bot_response = "Account ID not found. Please try again or type 'Speak to Agent' for assistance."
                action_needed = "awaiting_account_id" # Remain in this state
            
            session['auth_state'] = auth_state
            session['next_expected_input'] = action_needed

        elif auth_state == 2 and next_expected_input == 'otp':
            user_otp = user_message
            phone_to_validate = session.get('phone_number')
            customer_id_for_validation = session.get('customer_id')

            if phone_to_validate and customer_id_for_validation:
                is_valid_otp, otp_message = _validate_otp_internal(phone_to_validate, user_otp)
                
                if is_valid_otp:
                    auth_state = 3 # Authenticated!
                    bot_response = "OTP verified successfully! "
                    
                    if original_query_intent == "agent_escalation_request":
                        bot_response += "Now, please reiterate your need to speak to an agent."
                    elif original_query_intent:
                        bot_response += _process_account_query(original_query_intent, customer_id_for_validation)
                    else:
                        bot_response += " How else can I help you today? Please choose from the menu:\n1. My EMI\n2. Account Balance\n3. Loan Details"
                    
                    action_needed = "none" # Operation completed
                    session['original_query_intent'] = None # Clear intent
                    session['next_expected_input'] = 'menu_choice' if auth_state == 3 and not original_query_intent else 'none' # Reset for next interaction
                    
                else:
                    bot_response = f"OTP verification failed: {otp_message} Please try again."
                    action_needed = "awaiting_otp" # Remain in awaiting OTP state
            else:
                bot_response = "Session error during OTP verification. Please restart the conversation by typing your query."
                action_needed = "none"
                # Reset all session state
                session['auth_state'] = 0
                session['customer_id'] = None
                session['phone_number'] = None
                session['original_query_intent'] = None
                session['next_expected_input'] = 'menu_choice'

            session['auth_state'] = auth_state
            session['next_expected_input'] = action_needed

        elif auth_state == 3: # Authenticated - regular chat flow, can also accept direct queries
            classified_intent = classify_intent(user_message)
            if classified_intent == "unclear" and user_message:
                all_chats_in_session = get_all_chats_for_session(customer_id, chat_session_id)
                formatted_history = []
                for chat in all_chats_in_session:
                    formatted_history.append({"sender": chat['sender'], "content": chat['message_text']})
                formatted_history.append({"sender": "user", "content": user_message})
                classified_intent = bedrock_get_intent_from_text(formatted_history)

            if classified_intent in ["emi", "balance", "loan"]:
                bot_response = _process_account_query(classified_intent, customer_id)
                action_needed = "none"
            else:
                all_chats_in_session = get_all_chats_for_session(customer_id, chat_session_id)
                formatted_history = []
                for chat in all_chats_in_session:
                    formatted_history.append({"sender": chat['sender'], "content": chat['message_text']})
                formatted_history.append({"sender": "user", "content": user_message})
                
                bot_response = bedrock_generate_response(formatted_history, user_message)
                action_needed = "none"
            
            session['auth_state'] = auth_state # Should still be 3
            session['next_expected_input'] = 'menu_choice' # After a successful query, prompt with menu again for convenience

        else: # Fallback for unexpected states, reset to initial menu
            bot_response = "I'm sorry, I seem to have lost track. Let's start fresh. How can I help you today?\n1. My EMI\n2. Account Balance\n3. Loan Details\n\nPlease reply with the number of your choice."
            action_needed = "awaiting_menu_choice"
            session['auth_state'] = 0
            session['customer_id'] = None
            session['phone_number'] = None
            session['original_query_intent'] = None
            session['next_expected_input'] = 'menu_choice'


        save_chat_interaction(
            session_id=chat_session_id,
            customer_id=customer_id, # Will be None until authenticated
            message_text=bot_response,
            sender='bot',
            intent=classified_intent if classified_intent != "unclear" else "general_response"
        )
        
        return jsonify({
            "response": bot_response,
            "auth_state": session['auth_state'],
            "action_needed": action_needed
        })

    except Exception as e:
        logging.error(f"Critical error in web chat endpoint: {e}") 
        save_chat_interaction(
            session_id=chat_session_id,
            customer_id=customer_id,
            message_text="An internal error occurred. Please try again later.",
            sender='bot',
            intent='error'
        )
        return jsonify({"response": "An internal error occurred. Please try again later.", "auth_state": session.get('auth_state', 0), "action_needed": "none"}), 500


# --- WhatsApp Webhook ---
@app.route('/whatsapp/webhook', methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    whatsapp_phone_number = request.values.get('From', '').replace('whatsapp:', '')
    
    resp = MessagingResponse()
    msg = resp.message()

    logging.info(f"Received WhatsApp message from {whatsapp_phone_number}: '{incoming_msg}'")

    customer_id = get_or_create_customer_by_phone(whatsapp_phone_number)
    if not customer_id:
        msg.body("Sorry, I'm having trouble identifying you. Please try again later.")
        logging.error(f"Could not get or create customer for WhatsApp number {whatsapp_phone_number}.")
        return str(resp)

    whatsapp_session_id = f"whatsapp_{customer_id}"

    if 'whatsapp_sessions' not in app.config:
        app.config['whatsapp_sessions'] = {}
    
    user_whatsapp_session = app.config['whatsapp_sessions'].get(customer_id, {
        'auth_state': 0,
        'original_query_intent': None,
        'next_expected_input': 'menu_choice',
        'phone_number': whatsapp_phone_number
    })
    
    app.config['whatsapp_sessions'][customer_id] = user_whatsapp_session

    auth_state = user_whatsapp_session['auth_state']
    original_query_intent = user_whatsapp_session['original_query_intent']
    next_expected_input = user_whatsapp_session['next_expected_input']
    bot_reply_text = ""

    try:
        # Save user message
        save_chat_interaction(whatsapp_session_id, customer_id, incoming_msg, 'user', 'whatsapp_flow')

        # 👎 Direct feedback triggers escalation
        if incoming_msg == "👎":
            chat_history_for_summary = get_all_chats_for_session(customer_id, whatsapp_session_id)
            summary_text = get_chat_summary(chat_history_for_summary)
            summary_embedding = get_embedding(summary_text)

            conv_sid = create_conversation(customer_id)
            task_sid = create_task_for_handoff(
                customer_id=customer_id,
                session_id=whatsapp_session_id,
                summary=summary_text,
                customer_phone_number=whatsapp_phone_number,
                conversation_sid=conv_sid,
                channel_type="whatsapp"
            )
            if task_sid:
                save_unresolved_chat(customer_id, summary_text, summary_embedding, task_sid, status="pending")
                bot_reply_text = "👨‍💼 We're connecting you to a live agent. Please wait..."
                with Session() as db_session:
                    db_session.query(ChatInteraction).filter(
                        ChatInteraction.session_id == whatsapp_session_id,
                        ChatInteraction.customer_id == customer_id
                    ).update({"is_escalated": True, "conversation_sid": conv_sid}, synchronize_session=False)
                    db_session.commit()
            else:
                bot_reply_text = "❌ Sorry, we couldn't escalate your request. Please try again later."
            msg.body(bot_reply_text)
            return str(resp)

        # 🧠 Intent classification
        if classify_intent(incoming_msg) == "agent_escalation" or incoming_msg.lower() in ["speak to agent", "connect me to agent"]:
            if auth_state == 3:
                chat_history_for_summary = get_all_chats_for_session(customer_id, whatsapp_session_id)
                summary_text = get_chat_summary(chat_history_for_summary)
                summary_embedding = get_embedding(summary_text)
                conv_sid = create_conversation(customer_id)
                task_sid = create_task_for_handoff(
                    customer_id=customer_id,
                    session_id=whatsapp_session_id,
                    summary=summary_text,
                    customer_phone_number=whatsapp_phone_number,
                    conversation_sid=conv_sid,
                    channel_type="whatsapp"
                )
                if task_sid:
                    save_unresolved_chat(customer_id, summary_text, summary_embedding, task_sid, status="pending")
                    with Session() as db_session:
                        db_session.query(ChatInteraction).filter(
                            ChatInteraction.session_id == whatsapp_session_id,
                            ChatInteraction.customer_id == customer_id
                        ).update({"is_escalated": True, "conversation_sid": conv_sid}, synchronize_session=False)
                        db_session.commit()
                    bot_reply_text = "✅ Your query is now escalated to a human agent. Please wait..."
                    user_whatsapp_session['auth_state'] = 0
                    user_whatsapp_session['original_query_intent'] = None
                    user_whatsapp_session['next_expected_input'] = 'menu_choice'
                else:
                    bot_reply_text = "❌ Unable to connect to an agent now. Try again later."
            else:
                bot_reply_text = "🛡️ Please verify your account before we connect you to a human agent.\nEnter your Account ID:"
                user_whatsapp_session['auth_state'] = 1
                user_whatsapp_session['original_query_intent'] = "agent_escalation_request"
                user_whatsapp_session['next_expected_input'] = "account_id"

            save_chat_interaction(whatsapp_session_id, customer_id, bot_reply_text, "bot", "agent_escalation_response")
            msg.body(bot_reply_text + "\n\nPlease share your feedback: 👍 👎")
            return str(resp)

        # Main authentication-driven flow
        if auth_state == 0 and next_expected_input == 'menu_choice':
            if incoming_msg == '1':
                original_query_intent = 'emi'
                bot_reply_text = "Great! Please enter your Account ID to get EMI details."
                auth_state = 1
                next_expected_input = "account_id"
            elif incoming_msg == '2':
                original_query_intent = 'balance'
                bot_reply_text = "Sure! Please enter your Account ID to check balance."
                auth_state = 1
                next_expected_input = "account_id"
            elif incoming_msg == '3':
                original_query_intent = 'loan'
                bot_reply_text = "Sure! Please enter your Account ID to see your loan info."
                auth_state = 1
                next_expected_input = "account_id"
            else:
                bot_reply_text = "📋 Menu:\n1. My EMI\n2. Account Balance\n3. Loan Details\n\nReply with a number."
                next_expected_input = 'menu_choice'

        elif auth_state == 1 and next_expected_input == 'account_id':
            account_id = incoming_msg
            customer_info = fetch_customer_by_account_id(account_id)
            if customer_info:
                customer_id = customer_info['customer_id']
                user_whatsapp_session['customer_id'] = customer_id
                user_whatsapp_session['phone_number'] = customer_info['phone_number']
                otp_sent, otp_msg = _send_otp_internal(customer_info['phone_number'], customer_id)
                if otp_sent:
                    bot_reply_text = f"{otp_msg} Please enter the 6-digit OTP."
                    auth_state = 2
                    next_expected_input = "otp"
                else:
                    bot_reply_text = f"Failed to send OTP: {otp_msg}. Try again."
            else:
                bot_reply_text = "Account not found. Recheck ID or type 'Speak to Agent'."

        elif auth_state == 2 and next_expected_input == 'otp':
            is_valid, otp_msg = _validate_otp_internal(user_whatsapp_session['phone_number'], incoming_msg)
            if is_valid:
                auth_state = 3
                if original_query_intent:
                    bot_reply_text = "OTP verified!\n" + _process_account_query(original_query_intent, customer_id)
                else:
                    bot_reply_text = "OTP verified! What would you like to do?\n1. My EMI\n2. Account Balance\n3. Loan Details"
                original_query_intent = None
                next_expected_input = "menu_choice"
            else:
                bot_reply_text = f"❌ OTP failed: {otp_msg}. Try again."

        elif auth_state == 3:
            classified_intent = classify_intent(incoming_msg)
            if classified_intent in ["emi", "balance", "loan"]:
                bot_reply_text = _process_account_query(classified_intent, customer_id)
            else:
                history = get_all_chats_for_session(customer_id, whatsapp_session_id)
                messages = [{"sender": c['sender'], "content": c['message_text']} for c in history]
                messages.append({"sender": "user", "content": incoming_msg})
                bot_reply_text = bedrock_generate_response(messages, incoming_msg) or "I'm not sure I understood that."

            next_expected_input = "menu_choice"

        else:
            bot_reply_text = "Let's start fresh.\n1. My EMI\n2. Account Balance\n3. Loan Details"
            auth_state = 0
            original_query_intent = None
            next_expected_input = "menu_choice"

        # Save session and response
        user_whatsapp_session.update({
            'auth_state': auth_state,
            'original_query_intent': original_query_intent,
            'next_expected_input': next_expected_input
        })
        app.config['whatsapp_sessions'][customer_id] = user_whatsapp_session

        msg.body(bot_reply_text + "\n\nPlease share your feedback: 👍 👎")
        save_chat_interaction(whatsapp_session_id, customer_id, bot_reply_text, "bot", original_query_intent)
        return str(resp)

    except Exception as e:
        logging.error(f"Critical error in WhatsApp webhook: {e}")
        err_msg = "⚠️ Something went wrong. Please try again later."
        msg.body(err_msg)
        save_chat_interaction(whatsapp_session_id, customer_id, err_msg, "bot", "error")
        return str(resp)



# --- Agent Dashboard Endpoints ---
@app.route('/agent/unresolved_sessions', methods=['GET'])
def get_unresolved_sessions():
    session_db = Session()
    try:
        unresolved_docs = session_db.query(RAGDocument, Customer.full_name, Customer.phone_number).join(
            Customer, RAGDocument.customer_id == Customer.customer_id
        ).filter(
            RAGDocument.status.in_(['pending', 'in_progress'])
        ).order_by(RAGDocument.created_at.desc()).all()
        
        sessions_data = []
        for doc, customer_name, phone_number in unresolved_docs:
            latest_escalated_chat = session_db.query(ChatInteraction).filter(
                ChatInteraction.customer_id == doc.customer_id,
                ChatInteraction.is_escalated == True
            ).order_by(ChatInteraction.timestamp.desc()).first()

            session_id_for_chat_interface = latest_escalated_chat.session_id if latest_escalated_chat else "unknown"

            sessions_data.append({
                "document_id": str(doc.document_id),
                "customer_id": doc.customer_id,
                "session_id": session_id_for_chat_interface,
                "task_sid": doc.task_sid,
                "summary": doc.document_text,
                "status": doc.status,
                "created_at": doc.created_at.isoformat(),
                "customer_name": customer_name,
                "customer_phone": phone_number
            })
        
        return jsonify({"status": "success", "sessions": sessions_data})
    except Exception as e:
        logging.error(f"Error fetching unresolved sessions for dashboard: {e}")
        return jsonify({"status": "error", "message": "Failed to fetch unresolved sessions"}), 500
    finally:
        session_db.close()


@app.route('/agent/get_all_chats_for_session', methods=['POST'])
def get_all_chats_for_agent_session_route():
    customer_id = request.json.get('customer_id')
    session_id = request.json.get('session_id')

    if not customer_id or not session_id:
        return jsonify({"status": "error", "message": "Missing customer_id or session_id"}), 400
    
    chats = get_all_chats_for_session(customer_id, session_id)

    if chats is not None:
        return jsonify({"status": "success", "chats": chats})
    else:
        return jsonify({"status": "error", "message": "Failed to fetch chat history"}), 500

@app.route('/agent/send_message', methods=['POST'])
def agent_send_message():
    customer_id = request.json.get('customer_id')
    session_id = request.json.get('session_id')
    message_text = request.json.get('message')

    if not all([customer_id, session_id, message_text]):
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    customer = get_customer_by_id(customer_id)
    if not customer:
        return jsonify({"status": "error", "message": "Customer not found."}), 404
    
    conversation_sid = create_conversation(customer_id)

    if not conversation_sid:
        logging.error(f"No active conversation found for customer {customer_id} to send agent message.")
        return jsonify({"status": "error", "message": "No active conversation found for this customer."}), 404
    
    if customer.phone_number and customer.phone_number.startswith('whatsapp:'):
        add_whatsapp_participant_to_conversation(conversation_sid, customer.phone_number.replace('whatsapp:', ''))
    else:
        add_customer_participant_to_conversation(conversation_sid, customer_id)

    success = send_message_to_conversation(conversation_sid, "Agent", message_text)
    
    if success:
        save_chat_interaction(
            session_id=session_id,
            customer_id=customer_id,
            message_text=message_text,
            sender='agent',
            intent='agent_response',
            conversation_sid=conversation_sid
        )
        return jsonify({"status": "success", "message": "Message sent to customer."})
    else:
        return jsonify({"status": "error", "message": "Failed to send message via Twilio Conversations."}), 500

@app.route('/agent/update_session_status', methods=['POST'])
def agent_update_session_status():
    document_id = request.json.get('document_id')
    status = request.json.get('status')

    if not document_id or not status:
        return jsonify({"status": "error", "message": "Document ID and status are required"}), 400
    
    session_db = Session()
    try:
        rag_doc = session_db.query(RAGDocument).filter_by(document_id=document_id).first()
        if not rag_doc:
            return jsonify({"status": "error", "message": "Unresolved session document not found"}), 404
        
        rag_doc.status = status
        
        if status == 'resolved':
            if rag_doc.task_sid and TWILIO_TASK_ROUTER_WORKSPACE_SID:
                try:
                    twilio_client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
                        .tasks(rag_doc.task_sid).update(assignment_status='completed')
                    logging.info(f"Task Router Task {rag_doc.task_sid} completed.")
                except TwilioRestException as e:
                    logging.warning(f"Could not complete Task Router Task {rag_doc.task_sid}: {e}")
            
            latest_escalated_chat = session_db.query(ChatInteraction).filter(
                ChatInteraction.customer_id == rag_doc.customer_id,
                ChatInteraction.is_escalated == True,
                ChatInteraction.conversation_sid.isnot(None)
            ).order_by(ChatInteraction.timestamp.desc()).first()

            if latest_escalated_chat and latest_escalated_chat.conversation_sid:
                try:
                    twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                        .conversations(latest_escalated_chat.conversation_sid).update(state='closed')
                    logging.info(f"Twilio Conversation {latest_escalated_chat.conversation_sid} closed.")
                except TwilioRestException as e:
                    logging.warning(f"Could not close Twilio Conversation {latest_escalated_chat.conversation_sid}: {e}")
            
            if latest_escalated_chat and latest_escalated_chat.session_id:
                session_db.query(ChatInteraction).filter(
                    ChatInteraction.session_id == latest_escalated_chat.session_id,
                    ChatInteraction.customer_id == rag_doc.customer_id
                ).update({"is_escalated": False}, synchronize_session=False)
                logging.info(f"Marked chat interactions for session {latest_escalated_chat.session_id} as not escalated.")

        session_db.commit()
        logging.info(f"RAGDocument {document_id} status updated to {status}.")
        return jsonify({"status": "success", "message": f"Session status updated to {status}."})

    except Exception as e:
        session_db.rollback()
        logging.error(f"Error updating RAGDocument status for {document_id}: {e}")
        return jsonify({"status": "error", "message": "Failed to update session status."}), 500
    finally:
        session_db.close()


@app.route('/agent/check_customer_messages', methods=['POST'])
def agent_check_customer_messages():
    """Agent UI polls this to get new messages from the customer in a live chat."""
    data = request.json
    customer_id = data.get('customer_id')
    session_id = data.get('session_id')
    last_check_time_str = data.get('last_check_time')

    if not customer_id or not session_id or not last_check_time_str:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400
    
    try:
        last_check_time = datetime.fromisoformat(last_check_time_str)
        
        session_db = Session()
        new_messages = session_db.query(ChatInteraction).filter(
            ChatInteraction.customer_id == customer_id,
            ChatInteraction.session_id == session_id,
            ChatInteraction.sender == 'user',
            ChatInteraction.timestamp > last_check_time
        ).order_by(ChatInteraction.timestamp.asc()).all()
        
        messages = [{
            'message_text': msg.message_text,
            'timestamp': msg.timestamp.isoformat(),
            'session_id': msg.session_id,
            'sender': msg.sender
        } for msg in new_messages]
        
        return jsonify({
            "status": "success", 
            "messages": messages,
            "current_time": datetime.now(timezone.utc).isoformat()
        })
        
    except Exception as e:
        logging.error(f"Error checking for new customer messages in session {session_id}: {e}")
        return jsonify({"status": "error", "message": "Failed to check for new messages"}), 500
    finally:
        session_db.close()

if __name__ == '__main__':
    app.run(debug=app.debug, host='0.0.0.0', port=5000)

