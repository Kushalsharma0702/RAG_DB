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
from config import (
    TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID, TWILIO_PHONE,
    TWILIO_TASK_ROUTER_WORKSPACE_SID, TWILIO_TASK_ROUTER_WORKFLOW_SID # Import Task Router SIDs
)
from twilio_chat import create_conversation, send_message_to_conversation, create_task_for_handoff # Import twilio_chat functions

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

# Removed the internal _create_or_get_conversation_and_add_participants
# and _send_message_to_conversation functions from app.py
# because we now directly use the functions from twilio_chat.py.
# This centralizes Twilio-related logic.

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
        
        # Return customer_id in the response so it can be stored client-side
        return jsonify({
            "status": "success", 
            "message": reply,
            "customer_id": customer_id  # Add this line
        })
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
            
            # Use create_conversation from twilio_chat.py
            conversation_sid = create_conversation(
                user_id=str(customer_id)
            )

            if conversation_sid:
                # Send the conversation summary to Twilio using send_message_to_conversation
                send_message_to_conversation(
                    conversation_sid, 
                    author="System", 
                    message_body=f"A user ({customer_id}) has reported an unresolved issue and requires agent assistance.\n\nSummary:\n{summary_text}"
                )

                last_three_chats = get_last_three_chats(customer_id)
                if last_three_chats:
                    send_message_to_conversation(conversation_sid, author="System", message_body="--- Last 3 Chat Interactions ---")
                    for chat in last_three_chats:
                        send_message_to_conversation(
                            conversation_sid,
                            author=chat['sender'],
                            message_body=chat['message_text']
                        )
                
                # Create a Task Router Task for agent handoff
                task_sid = create_task_for_handoff(
                    customer_id=customer_id,
                    phone_number=customer_phone_number,
                    summary=summary_text,
                    recent_messages=last_three_chats, # Pass recent chat history for context in Task
                    conversation_sid=conversation_sid
                )

                if task_sid:
                    logging.info(f"🏆 Task Router Task {task_sid} created for customer {customer_id}.")
                    logging.info(f"✅ Messages sent to conversation {conversation_sid}")
                    logging.info(f"🔁 Chat routed to agent via Task Router. Conversation SID: {conversation_sid}")
                else:
                    logging.error(f"❌ Failed to create Task Router Task for customer {customer_id}.")
                
            else:
                logging.error("❌ Failed to create/get conversation for agent handoff.")

            print(f"Saved unresolved chat summary and embedding for customer {customer_id} (Session: {current_session_id})")
            return jsonify({"status": "success", "message": "Chat summary and embedding saved and routed via Task Router."})
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

from flask import render_template_string
from database import Session
from database import RAGDocument, Customer

@app.route("/agent-dashboard")
def serve_agent_dashboard():
    return send_from_directory(app.static_folder, 'agent_dashboard.html')

@app.route("/agent/unresolved_sessions")
def get_unresolved_sessions():
    session_db = Session()
    try:
        result = session_db.query(RAGDocument, Customer.phone_number).join(
            Customer, Customer.customer_id == RAGDocument.customer_id
        ).order_by(RAGDocument.created_at.desc()).limit(10).all()
        sessions = [{
            "customer_id": r.RAGDocument.customer_id,
            "document_text": r.RAGDocument.document_text,
            "created_at": r.RAGDocument.created_at.strftime("%Y-%m-%d %H:%M"),
            "phone_number": r.phone_number
        } for r in result]
        return jsonify({"status": "success", "sessions": sessions})
    except Exception as e:
        print("Error fetching unresolved sessions:", e)
        return jsonify({"status": "error", "message": "Could not fetch sessions"}), 500
    finally:
        session_db.close()

@app.route("/create_taskrouter_test_task", methods=["POST"])
def create_test_task():
    # This endpoint is for testing purposes and needs your actual SIDs.
    # Replace placeholders with your actual TWILIO_TASK_ROUTER_WORKSPACE_SID and TWILIO_TASK_ROUTER_WORKFLOW_SID
    # from config.py or environment variables.
    workspace_sid = TWILIO_TASK_ROUTER_WORKSPACE_SID
    workflow_sid = TWILIO_TASK_ROUTER_WORKFLOW_SID

    if not workspace_sid or not workflow_sid:
        return jsonify({"status": "error", "message": "Task Router SIDs are not configured."}), 500

    try:
        task = twilio_client.taskrouter.workspaces(workspace_sid).tasks.create(
            workflow_sid=workflow_sid,
            attributes=json.dumps({
                "type": "chat_handoff",
                "customer_id": "CID_TEST_123",
                "summary": "This is a test task created from Flask app.",
                "session_id": "test-session-12345",
                "phone_number": "+1234567890" # Example phone number
            })
        )
        return jsonify({"message": "Test Task created", "task_sid": task.sid}), 200
    except Exception as e:
        logging.error(f"Error creating test Task Router task: {e}")
        return jsonify({"status": "error", "message": f"Failed to create test task: {e}"}), 500


@app.route('/webhook/taskrouter_assignment', methods=['POST'])
def taskrouter_assignment():
    from flask import request, jsonify

    task_sid = request.form.get("TaskSid")
    worker_sid = request.form.get("WorkerSid")
    print(f"✅ Task assigned by Twilio TaskRouter!")
    print(f"Task SID: {task_sid}")
    print(f"Worker SID: {worker_sid}")

    # Required JSON response
    return jsonify(instruction="accept"), 200

@app.route("/connect_agent", methods=["POST"])
def connect_agent():
    """
    Endpoint to handle agent connection requests.
    This will save the chat history to the RAG document table and
    create a Twilio conversation for the handoff, then create a Task Router Task.
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
            
            # Create a Twilio conversation (agent not added here, Task Router will do it)
            conversation_sid = create_conversation(
                user_id=str(customer_id)
            )

            if conversation_sid:
                # Send the conversation summary to Twilio
                send_message_to_conversation(
                    conversation_sid, 
                    author="System", 
                    message_body=f"A user ({customer_id}) has requested agent assistance.\n\nSummary:\n{summary_text}"
                )

                # Include the last few messages for context
                send_message_to_conversation(conversation_sid, author="System", message_body="--- Recent Chat History ---")
                # Only send the last 5 messages to avoid cluttering the agent interface
                recent_messages_for_context = chat_history[-5:] if len(chat_history) > 5 else chat_history
                for msg in recent_messages_for_context:
                    send_message_to_conversation(
                        conversation_sid,
                        author=msg['sender'],
                        message_body=msg['content']
                    )
                
                # Create a Task Router Task for agent handoff
                task_sid = create_task_for_handoff(
                    customer_id=customer_id,
                    phone_number=customer_phone_number,
                    summary=summary_text,
                    recent_messages=recent_messages_for_context, # Pass recent chat history for context in Task
                    conversation_sid=conversation_sid
                )

                if task_sid:
                    logging.info(f"🏆 Task Router Task {task_sid} created for customer {customer_id}.")
                    logging.info(f"✅ Messages sent to conversation {conversation_sid}")
                    logging.info(f"🔁 Chat routed to agent via Task Router. Conversation SID: {conversation_sid}")
                    
                    # Save the interaction in the database
                    save_chat_interaction(
                        session_id=current_session_id, 
                        sender='system', 
                        message_text="Conversation routed to human agent via Task Router.", 
                        customer_id=customer_id, 
                        stage='agent_handoff'
                    )
                    
                    return jsonify({"status": "success", "message": "Chat routed to agent successfully."})
                else:
                    logging.error(f"❌ Failed to create Task Router Task for customer {customer_id}.")
                    return jsonify({"status": "error", "message": "Failed to create agent task. Please try again later."}), 500
            else:
                logging.error("❌ Failed to create/get conversation for agent handoff.")
                return jsonify({"status": "error", "message": "Failed to connect with agent. Please try again later."}), 500
        else:
            logging.error(f"Failed to generate embedding for chat summary for customer {customer_id}.")
            return jsonify({"status": "error", "message": "Failed to generate summary for agent handoff."}), 500

    except Exception as e:
        logging.error(f"Error in connect_agent: {e}")
        return jsonify({"status": "error", "message": "An error occurred during agent connection."}), 500

@app.route("/agent-chat-interface")
def serve_agent_chat_interface():
    return send_from_directory(app.static_folder, 'agent_chat_interface.html')

@app.route("/agent/send_message", methods=["POST"])
def agent_send_message():
    """
    Endpoint for agent to send messages to the customer conversation.
    """
    customer_id = request.json.get("customer_id")
    session_id = request.json.get("session_id")
    message = request.json.get("message")
    
    if not customer_id or not message:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400
    
    # Validate session_id to prevent "undefined" errors
    if not session_id or session_id == "undefined":
        # Generate a valid UUID for this interaction
        session_id = str(uuid.uuid4())
        
    try:
        # First, save the agent message to our database
        save_chat_interaction(
            session_id=session_id,
            sender='agent',
            message_text=message,
            customer_id=customer_id,
            stage='agent_response'
        )
        
        # Get customer information from database to retrieve phone number
        customer_data = fetch_customer_by_id(customer_id)
        if not customer_data:
            return jsonify({"status": "error", "message": "Customer not found"}), 404
            
        # Create or get the Twilio conversation (customer participant only)
        conversation_sid = create_conversation(
            user_id=str(customer_id)
        )
        
        if conversation_sid:
            # Send the agent message to the conversation
            send_message_to_conversation(
                conversation_sid,
                author="Agent",
                message_body=message
            )
            
            logging.info(f"✅ Agent message sent to conversation {conversation_sid}")
            return jsonify({"status": "success", "message": "Message sent successfully"})
        else:
            return jsonify({"status": "error", "message": "Failed to send message to conversation"}), 500
            
    except Exception as e:
        logging.error(f"Error in agent_send_message: {e}")
        return jsonify({"status": "error", "message": "An error occurred while sending the message"}), 500

# Add this function to fetch customer by ID
def fetch_customer_by_id(customer_id):
    session_db = Session()
    try:
        customer = session_db.query(Customer).filter(Customer.customer_id == customer_id).first()
        if customer:
            # Note: The Customer model doesn't seem to have 'account_id' directly.
            # Assuming it can be fetched via customer_id from a related table if needed elsewhere.
            return {
                'customer_id': customer.customer_id,
                'phone_number': customer.phone_number,
                # 'account_id': customer.account_id # If Customer model had account_id
            }
        return None
    except Exception as e:
        logging.error(f"Error fetching customer by ID: {e}")
        return None
    finally:
        session_db.close()
@app.route('/agent/update_task_status', methods=['POST'])
def update_task_status():
    """
    Update the status of a Twilio Task Router task
    """
    task_id = request.json.get('task_id')
    status = request.json.get('status')
    
    if not task_id or not status:
        return jsonify({"status": "error", "message": "Task ID and status are required"}), 400
    
    # Check if this is a Twilio Task Router task or a local document ID
    if task_id.startswith('WT'):
        # It's a Twilio Task ID
        try:
            # Update the Twilio Task Router task
            task = twilio_client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
                .tasks(task_id) \
                .update(
                    assignment_status=status if status == 'completed' else 'pending',
                    reason=f"Agent marked as {status}"
                )
            
            # Also update our local database to keep track of status
            session_db = Session()
            rag_doc = session_db.query(RAGDocument).filter(RAGDocument.task_id == task_id).first()
            if rag_doc:
                rag_doc.status = status
                session_db.commit()
            session_db.close()
            
            logging.info(f"✅ Task {task_id} status updated to {status}")
            return jsonify({"status": "success", "message": f"Task status updated to {status}"})
        
        except Exception as e:
            logging.error(f"❌ Error updating task status: {e}")
            return jsonify({"status": "error", "message": f"Failed to update task status: {e}"}), 500
    else:
        # It's a local document ID
        try:
            session_db = Session()
            rag_doc = session_db.query(RAGDocument).filter(RAGDocument.document_id == task_id).first()
            if not rag_doc:
                return jsonify({"status": "error", "message": "Document not found"}), 404
            
            rag_doc.status = status
            session_db.commit()
            session_db.close()
            
            logging.info(f"✅ Document {task_id} status updated to {status}")
            return jsonify({"status": "success", "message": f"Document status updated to {status}"})
        
        except Exception as e:
            logging.error(f"❌ Error updating document status: {e}")
            return jsonify({"status": "error", "message": f"Failed to update document status: {e}"}), 500
@app.route('/check_agent_messages', methods=['POST'])
def check_agent_messages():
    """
    Endpoint for user interface to check for new agent messages.
    Returns messages sent by agents after the last_check_time.
    """
    customer_id = request.json.get('customer_id')
    last_check_time = request.json.get('last_check_time')
    
    if not customer_id or not last_check_time:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400
    
    try:
        from datetime import datetime
        from sqlalchemy import and_
        from database import ChatInteraction
        
        # Convert string to datetime
        last_check = datetime.fromisoformat(last_check_time.replace('Z', '+00:00'))
        
        # Query for new agent messages
        session_db = Session()
        new_messages = session_db.query(ChatInteraction).filter(
            and_(
                ChatInteraction.customer_id == customer_id,
                ChatInteraction.sender == 'agent',
                ChatInteraction.timestamp > last_check
            )
        ).order_by(ChatInteraction.timestamp.asc()).all()
        
        # Format messages for response
        messages = [{
            'message_text': msg.message_text,
            'timestamp': msg.timestamp.isoformat(),
            'session_id': msg.session_id
        } for msg in new_messages]
        
        session_db.close()
        
        return jsonify({
            "status": "success", 
            "messages": messages
        })
        
    except Exception as e:
        logging.error(f"Error checking for agent messages: {e}")
        return jsonify({"status": "error", "message": "Failed to check for new messages"}), 500

if __name__ == '__main__':
     app.run(debug=True, host='0.0.0.0', port=5504)
    # pass