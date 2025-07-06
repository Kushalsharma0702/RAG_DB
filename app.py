from flask import Flask, request, jsonify, session, send_from_directory
from dotenv import load_dotenv
import os
import uuid
import logging
import json
from datetime import datetime
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

app = Flask(__name__, static_folder='frontend')
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

        # Check if index.html exists
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

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5504)
