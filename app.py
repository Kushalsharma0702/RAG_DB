from flask import Flask, request, jsonify, session, send_from_directory
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime
from flask_cors import CORS
import logging
from twilio.rest import Client
import json

from otp_manager import send_otp, validate_otp
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
from config import TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID, TWILIO_PHONE

load_dotenv()

app = Flask(__name__, static_folder='frontend')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_super_secret_key")
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

with app.app_context():
    create_tables()
    try:
        run_migration()
        print("Database setup completed successfully")
    except Exception as e:
        print(f"Warning: Database migration encountered an issue: {e}")
        print("The application will continue, but some features may not work correctly.")

def _create_or_get_conversation_and_add_participants(customer_id, customer_phone_number):
    conversation_sid = None
    conversation_unique_name = f"customer_{customer_id}_handoff"
    friendly_name = f"Chat with {customer_id}"

    try:
        try:
            conversation = twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                            .conversations.get(conversation_unique_name).fetch()
            conversation_sid = conversation.sid
            logging.info(f"Existing conversation found for {customer_id}: {conversation_sid}")
        except Exception as e_get:
            # Modified this line to be more robust
            if "not found" in str(e_get).lower():
                logging.info(f"No existing conversation with unique name '{conversation_unique_name}'. Attempting to create a new one.")
                try:
                    conversation = twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                                    .conversations.create(
                                        friendly_name=friendly_name,
                                        unique_name=conversation_unique_name,
                                        attributes=json.dumps({"customer_id": customer_id})
                                    )
                    conversation_sid = conversation.sid
                    logging.info(f"New conversation created for {customer_id} in service {TWILIO_CONVERSATIONS_SERVICE_SID}: {conversation_sid}")
                except Exception as e_create:
                    logging.error(f"❌ Error creating new conversation for {customer_id}: {e_create}")
                    return None
            else:
                logging.error(f"❌ Unexpected error fetching conversation by unique name: {e_get}")
                return None

        if conversation_sid:
            try:
                twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                    .conversations(conversation_sid) \
                    .participants.create(identity=customer_id, attributes=json.dumps({"type": "customer", "phone_number": customer_phone_number}))
                logging.info(f"Customer {customer_id} added as participant to conversation {conversation_sid}")
            except Exception as e_customer_add:
                if "Participant already exists" not in str(e_customer_add):
                    logging.error(f"❌ Error adding customer {customer_id} to conversation {conversation_sid}: {e_customer_add}")
                else:
                    logging.info(f"Customer {customer_id} already participant in conversation {conversation_sid}")

            try:
                agent_identity = "live_agent_1"
                twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                    .conversations(conversation_sid) \
                    .participants.create(identity=agent_identity, attributes=json.dumps({"type": "agent"}))
                logging.info(f"Agent {agent_identity} added as participant to conversation {conversation_sid}")
            except Exception as e_agent_add:
                if "Participant already exists" not in str(e_agent_add):
                    logging.error(f"❌ Error adding agent {agent_identity} to conversation {conversation_sid}: {e_agent_add}")
                else:
                    logging.info(f"Agent {agent_identity} already participant in conversation {conversation_sid}")
        
        return conversation_sid

    except Exception as e:
        logging.error(f"❌ General error in _create_or_get_conversation_and_add_participants: {e}")
        return None

def _send_message_to_conversation(conversation_sid, author, message_body):
    try:
        message = twilio_client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                        .conversations(conversation_sid) \
                        .messages.create(author=author, body=message_body)
        logging.info(f"Message sent to conversation {conversation_sid} by {author}")
        return message.sid
    except Exception as e:
        logging.error(f"❌ Error sending message to conversation {conversation_sid}: {e}")
        return None

@app.route('/')
def serve_frontend():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/send_otp', methods=['POST'])
def otp_request():
    account_id_input = request.json.get("account_id")
    if not account_id_input:
        logging.warning("❌ OTP request failed: No account ID provided.")
        return jsonify({"status": "error", "message": "Account ID is required."}), 400

    current_session_id = str(session.get('session_id', uuid.uuid4()))
    session['session_id'] = current_session_id

    save_chat_interaction(session_id=current_session_id, sender='user', message_text=f"Account ID: {account_id_input}", customer_id=None, stage='account_id_entry')

    customer_account = fetch_customer_by_account(account_id_input)

    if not customer_account:
        logging.warning(f"❌ OTP request failed: Account ID {account_id_input} not found.")
        reply = "Account ID not found. Please try again or contact support."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=None, stage='account_id_not_found')
        return jsonify({"status": "error", "message": reply}), 404

    customer_id = customer_account['customer_id']
    phone_number = customer_account['phone_number']

    session['customer_id'] = customer_id
    session['account_id'] = account_id_input
    session['phone_number'] = phone_number

    otp = send_otp(phone_number)
    if otp:
        logging.info(f"🏆 OTP sent to {phone_number} for account_id={account_id_input}")
        reply = f"OTP sent to number ending with {phone_number[-4:]}"
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='otp_sent')
        return jsonify({"status": "success", "message": reply, "phone_number": phone_number})
    else:
        logging.error(f"❌ Failed to send OTP to {phone_number}")
        reply = "Failed to send OTP. Please try again."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='otp_send_failed')
        return jsonify({"status": "error", "message": reply}), 500

@app.route('/verify_otp', methods=['POST'])
def otp_verification():
    user_otp = request.json.get("otp")
    phone_number = session.get('phone_number')
    current_session_id = str(session.get('session_id', uuid.uuid4()))
    customer_id = session.get('customer_id')

    if not phone_number or not user_otp:
        return jsonify({"status": "error", "message": "OTP is required."}), 400

    save_chat_interaction(session_id=current_session_id, sender='user', message_text=f"OTP: {user_otp}", customer_id=customer_id, stage='otp_attempt')

    is_valid, message = validate_otp(phone_number, user_otp)
    if is_valid:
        logging.info(f"🏆 OTP verified for phone {phone_number}")
        reply = "OTP validated successfully."
        session['authenticated'] = True
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='otp_verified')
        return jsonify({"status": "success", "message": reply})
    else:
        logging.warning(f"❌ OTP verification failed for phone {phone_number}")
        reply = message or "Invalid OTP. Please try again."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='otp_failed')
        return jsonify({"status": "error", "message": reply}), 401

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json.get("message")
    chat_history = request.json.get("chat_history", [])
    current_session_id = str(session.get('session_id', uuid.uuid4()))
    customer_id = session.get('customer_id')
    account_id = session.get('account_id')

    save_chat_interaction(session_id=current_session_id, sender='user', message_text=user_message, customer_id=customer_id, stage='chat_message')

    logging.info(f"🏆 Chat request received for account_id={account_id}, customer_id={customer_id}")

    if not session.get('authenticated'):
        intent = classify_intent(user_message)
        if intent == "unclear":
            try:
                intent = get_intent_from_text(chat_history)
            except Exception as e:
                logging.error(f"Error with ML intent classification: {e}")
        
        if intent in ['emi', 'balance', 'loan']:
            reply = "Understood. To proceed, please enter your Account ID:"
            save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='awaiting_account_id', intent=intent)
            session['pending_intent'] = intent
            session['pending_message'] = user_message
            return jsonify({"status": "success", "reply": reply})
        else:
            reply = "Hello! I am your financial assistant. You can ask me about your EMI, account balance, or loan details. You can also select an option below."
            save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='initial_greeting', intent=intent)
            return jsonify({"status": "success", "reply": reply})

    logging.info(f"Session pending_intent before pop: {session.get('pending_intent')}")
    query_type = session.pop('pending_intent', None)
    
    if not query_type:
        query_type = classify_intent(user_message)
        logging.info(f"Rule-based intent classification: {query_type}")
        
        if query_type == 'unclear':
            try:
                single_message = [{"sender": "user", "content": user_message}]
                intent = get_intent_from_text(single_message)
                query_type = intent
                logging.info(f"ML-based intent from current message: {query_type}")
            except Exception as e:
                logging.error(f"Error with ML intent classification: {e}")

    logging.info(f"🏆 Using query_type={query_type} for account_id={account_id}")

    if query_type == 'unclear':
        # When we can't determine the intent, offer to connect with agent
        reply = "I'm not sure what financial information you're looking for. I can provide information about EMI, account balance, or loan details."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='intent_unclear', intent=query_type)
        return jsonify({"status": "success", "reply": reply, "needs_agent": True})

    data = fetch_data(query_type, account_id)
    if not data:
        # When we can't fetch data, offer to connect with agent
        logging.warning(f"❌ Data fetch failed for {query_type} (account_id={account_id})")
        reply = f"I couldn't find any information for your {query_type} query. This could be because the data doesn't exist in our system or there might be an issue accessing it."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='query_failed', intent=query_type)
        return jsonify({"status": "success", "reply": reply, "needs_agent": True})

    # Try to handle complex or edge cases
    if "out_of_scope" in user_message.lower() or any(word in user_message.lower() for word in ["complex", "difficult", "complicated", "help", "agent", "human", "talk", "speak"]):
        # User may be asking for something complex or directly requesting an agent
        logging.info(f"Complex or agent request detected: {user_message}")
        reply = "This seems like a complex query that might be better handled by one of our human agents."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='complex_query', intent=query_type)
        return jsonify({"status": "success", "reply": reply, "needs_agent": True})

    # Normal successful flow
    logging.info(f"🏆 Data fetched for {query_type} (account_id={account_id})")
    reply = generate_response(query_type, data, chat_history)
    save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='query_resolved', intent=query_type)

    return jsonify({"status": "success", "reply": reply})

@app.route("/summarize_chat", methods=["POST"])
def summarize_chat_route():
    chat_history = request.json.get("chat_history", [])
    customer_id = session.get('customer_id')
    customer_phone_number = session.get('phone_number')
    current_session_id = str(session.get('session_id', uuid.uuid4()))

    if not customer_id:
        print("Warning: Customer ID not found in session for summarization.")
        return jsonify({"status": "error", "message": "Customer ID not found in session. Summary not saved."}), 400

    try:
        summary_text = get_chat_summary(chat_history)
        summary_embedding = get_embedding(summary_text)

        if summary_embedding is not None:
            save_unresolved_chat(
                customer_id=customer_id,
                summary=summary_text,
                embedding=summary_embedding
            )
            
            conversation_sid = _create_or_get_conversation_and_add_participants(
                customer_id=str(customer_id),
                customer_phone_number=customer_phone_number
            )

            if conversation_sid:
                _send_message_to_conversation(
                    conversation_sid, 
                    author="System", 
                    message_body=f"A user ({customer_id}) has reported an unresolved issue and requires agent assistance.\n\nSummary:\n{summary_text}"
                )

                last_three_chats = get_last_three_chats(customer_id)
                if last_three_chats:
                    _send_message_to_conversation(conversation_sid, author="System", message_body="--- Last 3 Chat Interactions ---")
                    for chat in last_three_chats:
                        _send_message_to_conversation(
                            conversation_sid,
                            author=chat['sender'],
                            message_body=chat['message_text']
                        )
                
                logging.info(f"✅ Messages sent to conversation {conversation_sid}")
                logging.info(f"🔁 Chat routed to agent. Conversation SID: {conversation_sid}")
            else:
                logging.error("❌ Failed to create/get conversation or add participants for agent handoff.")

            print(f"Saved unresolved chat summary and embedding for customer {customer_id} (Session: {current_session_id})")
            return jsonify({"status": "success", "message": "Chat summary and embedding saved."})
        else:
            print(f"Failed to generate embedding for chat summary for customer {customer_id}.")
            return jsonify({"status": "error", "message": "Failed to generate embedding for summary."}), 500

    except Exception as e:
        print(f"Error in summarize_chat_route: {e}")
        return jsonify({"status": "error", "message": "An error occurred during chat summarization."}), 500

@app.route("/agent/get_last_chats", methods=["POST"])
def get_agent_summary():
    customer_id = request.json.get("customer_id")
    if not customer_id:
        return jsonify({"status": "error", "message": "Customer ID is required."}), 400
    chats = get_last_three_chats(customer_id)
    return jsonify({"status": "success", "chats": chats})

@app.route("/ozonetel_voice", methods=["POST"])
def handle_ozonetel_voice():
    data = request.form
    phone_number = data.get("caller_id")
    dtmf_input = data.get("user_input")
    stage = data.get("stage")

    if stage == "account_id":
        session['phone_number'] = phone_number
        session['account_id'] = dtmf_input
        result = fetch_customer_by_account(dtmf_input)
        if result:
            session['customer_id'] = result['customer_id']
            send_otp(result['phone_number'])
            return "OTP sent via SMS. Please enter it."
        else:
            return "Account ID not found. Try again."
    elif stage == "otp":
        valid, msg = validate_otp(session.get('phone_number'), dtmf_input)
        if valid:
            session['authenticated'] = True
            return "OTP verified. Please speak your query."
        else:
            return msg
    elif stage == "query":
        query_type = classify_intent(dtmf_input)
        result = fetch_data(query_type, session.get('account_id'))
        if not result:
            return f"No data found for {query_type}"
        answer = generate_response(query_type, result, [])
        return answer
    else:
        return "Invalid stage."

@app.route("/connect_agent", methods=["POST"])
def connect_agent():
    """
    Endpoint to handle agent connection requests.
    This will save the chat history to the RAG document table and
    create a Twilio conversation for the handoff.
    """
    chat_history = request.json.get("chat_history", [])
    customer_id = session.get('customer_id')
    customer_phone_number = session.get('phone_number')
    current_session_id = str(session.get('session_id', uuid.uuid4()))

    if not customer_id:
        logging.warning("Warning: Customer ID not found in session for agent connection.")
        return jsonify({"status": "error", "message": "Customer ID not found in session."}), 400

    try:
        # Generate a summary of the conversation
        summary_text = get_chat_summary(chat_history)
        summary_embedding = get_embedding(summary_text)

        # Save the conversation summary to RAG document table
        if summary_embedding is not None:
            save_unresolved_chat(
                customer_id=customer_id,
                summary=summary_text,
                embedding=summary_embedding
            )
            
            # Create a Twilio conversation and add the customer and agent
            conversation_sid = _create_or_get_conversation_and_add_participants(
                customer_id=str(customer_id),
                customer_phone_number=customer_phone_number
            )

            if conversation_sid:
                # Send the conversation summary to Twilio
                _send_message_to_conversation(
                    conversation_sid, 
                    author="System", 
                    message_body=f"A user ({customer_id}) has requested agent assistance.\n\nSummary:\n{summary_text}"
                )

                # Include the last few messages for context
                _send_message_to_conversation(conversation_sid, author="System", message_body="--- Recent Chat History ---")
                # Only send the last 5 messages to avoid cluttering the agent interface
                recent_messages = chat_history[-5:] if len(chat_history) > 5 else chat_history
                for msg in recent_messages:
                    _send_message_to_conversation(
                        conversation_sid,
                        author=msg['sender'],
                        message_body=msg['content']
                    )
                
                logging.info(f"✅ Messages sent to conversation {conversation_sid}")
                logging.info(f"🔁 Chat routed to agent. Conversation SID: {conversation_sid}")
                
                # Save the interaction in the database
                save_chat_interaction(
                    session_id=current_session_id, 
                    sender='system', 
                    message_text="Conversation routed to human agent.", 
                    customer_id=customer_id, 
                    stage='agent_handoff'
                )
                
                return jsonify({"status": "success", "message": "Chat routed to agent successfully."})
            else:
                logging.error("❌ Failed to create/get conversation for agent handoff.")
                return jsonify({"status": "error", "message": "Failed to connect with agent. Please try again later."}), 500
        else:
            logging.error(f"Failed to generate embedding for chat summary for customer {customer_id}.")
            return jsonify({"status": "error", "message": "Failed to generate summary for agent handoff."}), 500

    except Exception as e:
        logging.error(f"Error in connect_agent: {e}")
        return jsonify({"status": "error", "message": "An error occurred during agent connection."}), 500


if __name__ == '__main__':
     app.run(debug=True, host='0.0.0.0', port=5504)
    # pass