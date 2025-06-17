from flask import Flask, request, jsonify, session, send_from_directory
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime
from flask_cors import CORS
import logging

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
from db_migration import run_migration  # Import the migration function

load_dotenv()

app = Flask(__name__, static_folder='frontend')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_super_secret_key")
CORS(app)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Ensure tables are created and migrated when the app starts
with app.app_context():
    create_tables()
    try:
        run_migration()  # Run the migration to fix any schema issues
        print("Database setup completed successfully")
    except Exception as e:
        print(f"Warning: Database migration encountered an issue: {e}")
        print("The application will continue, but some features may not work correctly.")

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
        # Try rule-based classifier first for more reliability
        intent = classify_intent(user_message)
        if intent == "unclear":
            # Fall back to ML-based classifier
            try:
                intent = get_intent_from_text(chat_history)
            except Exception as e:
                logging.error(f"Error with ML intent classification: {e}")
                # Keep the rule-based result if ML fails
        
        if intent in ['emi', 'balance', 'loan']:
            reply = "Understood. To proceed, please enter your Account ID:"
            save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='awaiting_account_id', intent=intent)
            session['pending_intent'] = intent
            session['pending_message'] = user_message  # Store the original message
            return jsonify({"status": "success", "reply": reply})
        else:
            reply = "Hello! I am your financial assistant. You can ask me about your EMI, account balance, or loan details. You can also select an option below."
            save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='initial_greeting', intent=intent)
            return jsonify({"status": "success", "reply": reply})

    # For intent classification, use a simplified approach that looks at the most recent user message
    logging.info(f"Session pending_intent before pop: {session.get('pending_intent')}")
    query_type = session.pop('pending_intent', None)
    
    if not query_type:
        # Try rule-based classifier first
        query_type = classify_intent(user_message)
        logging.info(f"Rule-based intent classification: {query_type}")
        
        # If still unclear, try ML-based
        if query_type == 'unclear':
            try:
                # Create a single-message list to focus intent classification on current message
                single_message = [{"sender": "user", "content": user_message}]
                intent = get_intent_from_text(single_message)
                query_type = intent
                logging.info(f"ML-based intent from current message: {query_type}")
            except Exception as e:
                logging.error(f"Error with ML intent classification: {e}")
                # Keep the rule-based result

    logging.info(f"🏆 Using query_type={query_type} for account_id={account_id}")

    # If we still have an unclear intent, provide guidance to the user
    if query_type == 'unclear':
        reply = "I'm not sure what financial information you're looking for. Could you please specify if you want to know about your EMI, account balance, or loan details?"
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='intent_unclear', intent=query_type)
        return jsonify({"status": "success", "reply": reply})

    data = fetch_data(query_type, account_id)
    if not data:
        logging.warning(f"❌ Data fetch failed for {query_type} (account_id={account_id})")
        reply = f"I couldn't find any information for your {query_type} query. Please check if the details are correct or contact support."
        save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='query_failed', intent=query_type)
        return jsonify({"status": "success", "reply": reply})

    logging.info(f"🏆 Data fetched for {query_type} (account_id={account_id})")
    reply = generate_response(query_type, data, chat_history)
    save_chat_interaction(session_id=current_session_id, sender='bot', message_text=reply, customer_id=customer_id, stage='query_resolved', intent=query_type)

    return jsonify({"status": "success", "reply": reply})

@app.route("/summarize_chat", methods=["POST"])
def summarize_chat_route():
    chat_history = request.json.get("chat_history", [])
    customer_id = session.get('customer_id')
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5504)