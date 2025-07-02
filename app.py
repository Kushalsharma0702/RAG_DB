from flask import Flask, request, jsonify, session, send_from_directory
from dotenv import load_dotenv
import os
import uuid
from datetime import datetime
from flask_cors import CORS
import logging
from twilio.rest import Client
import json
from flask_socketio import SocketIO, emit, join_room
# Add this at the top of app.py
from database import ClientInteraction
# from models import ClientInteraction  # Ensure this is the correct class name
from sqlalchemy.orm import Session
import logging
from flask import jsonify
import uuid
from app_socketio import get_or_create_conversation
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
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID, TWILIO_PHONE,
    TWILIO_TASK_ROUTER_WORKSPACE_SID, TWILIO_TASK_ROUTER_WORKFLOW_SID # Import Task Router SIDs
)
from twilio_chat import create_conversation, send_message_to_conversation, create_task_for_handoff # Import twilio_chat functions

load_dotenv()

app = Flask(__name__, static_folder='frontend')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_super_secret_key")
CORS(app)

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
                    
                    # Notify all agents via Socket.IO
                    socketio.emit('new_escalated_chat', {
                        'customer_id': customer_id,
                        'timestamp': datetime.now().isoformat(),
                        'summary': summary_text[:100] + '...',  # Send a preview
                        'document_id': document_id if 'document_id' in locals() else None,
                        'task_id': task_sid
                    }, room='agent_room')
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
    """Serve the agent dashboard interface"""
    return send_from_directory(app.static_folder, 'agent_dashboard.html')

@app.route("/agent/unresolved_sessions")
def unresolved_sessions():
    session_db = Session()
    try:
        sessions = session_db.query(RAGDocument).filter(
            RAGDocument.status.in_(['pending', 'in-progress'])
        ).order_by(RAGDocument.created_at.desc()).all()
        result = []
        for s in sessions:
            result.append({
                'customer_id': s.customer_id,
                'document_id': str(s.document_id),
                'task_id': getattr(s, 'task_id', None),
                'status': s.status,
                'created_at': s.created_at.isoformat() if s.created_at else None,
                'document_text': s.document_text,
                'phone_number': getattr(s, 'phone_number', None)
            })
        return jsonify({'status': 'success', 'sessions': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
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

@app.route('/webhook/taskrouter', methods=['POST'])
def taskrouter_webhook():
    """
    Webhook endpoint for TaskRouter events
    This will receive events when tasks are created, assigned, completed, etc.
    """
    try:
        event_type = request.form.get('EventType')
        task_sid = request.form.get('TaskSid')
        workspace_sid = request.form.get('WorkspaceSid')
        
        # Log all incoming webhook events
        logging.info(f"Received TaskRouter webhook: {event_type} for task {task_sid}")
        
        # Handle different event types
        if event_type == 'task.created':
            # A new task was created
            task_attributes_json = request.form.get('TaskAttributes')
            if task_attributes_json:
                task_attributes = json.loads(task_attributes_json)
                customer_id = task_attributes.get('customer_id')
                
                # Notify all agents via Socket.IO
                socketio.emit('task_created', {
                    'task_sid': task_sid,
                    'customer_id': customer_id,
                    'attributes': task_attributes
                }, room='agent_room')
                
        elif event_type == 'task.assigned':
            # A task was assigned to an agent
            worker_sid = request.form.get('WorkerSid')
            task_attributes_json = request.form.get('TaskAttributes')
            
            if task_attributes_json:
                task_attributes = json.loads(task_attributes_json)
                customer_id = task_attributes.get('customer_id')
                
                # Notify all agents via Socket.IO
                socketio.emit('task_assigned', {
                    'task_sid': task_sid,
                    'worker_sid': worker_sid,
                    'customer_id': customer_id
                }, room='agent_room')
                
        elif event_type == 'task.completed':
            # A task was completed
            task_attributes_json = request.form.get('TaskAttributes')
            if task_attributes_json:
                task_attributes = json.loads(task_attributes_json)
                customer_id = task_attributes.get('customer_id')
                
                # Update the status in our database
                session_db = Session()
                rag_doc = session_db.query(RAGDocument).filter(RAGDocument.task_id == task_sid).first()
                if rag_doc:
                    rag_doc.status = 'resolved'
                    session_db.commit()
                session_db.close()
                
                # Notify all agents via Socket.IO
                socketio.emit('task_completed', {
                    'task_sid': task_sid,
                    'customer_id': customer_id
                }, room='agent_room')
        
        # Return 200 OK for all webhook requests
        return '', 200
        
    except Exception as e:
        logging.error(f"Error processing TaskRouter webhook: {e}")
        return '', 500

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
            document_id = save_unresolved_chat(
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
                    recent_messages=recent_messages_for_context,
                    conversation_sid=conversation_sid
                )

                if task_sid:
                    # Update the RAG document with the task_sid
                    session_db = Session()
                    rag_doc = session_db.query(RAGDocument).filter(
                        RAGDocument.document_id == document_id
                    ).first()
                    if rag_doc:
                        rag_doc.task_id = task_sid
                        rag_doc.status = 'pending'
                        session_db.commit()
                    session_db.close()
                    
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
                    
                    # Notify all agents via Socket.IO
                    socketio.emit('new_escalated_chat', {
                        'customer_id': customer_id,
                        'timestamp': datetime.now().isoformat(),
                        'summary': summary_text[:100] + '...',  # Send a preview
                        'document_id': document_id if 'document_id' in locals() else None,
                        'task_id': task_sid
                    }, room='agent_room')
                    
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

@app.route('/agent/send_message', methods=['POST'])
def agent_send_message():
    data = request.get_json()
    customer_id = data.get('customer_id')
    message = data.get('message')
    if not customer_id or not message:
        return jsonify({'status': 'error', 'message': 'Missing customer_id or message'}), 400

    try:
        # Fetch active conversation for customer
        conversation_sid = get_or_create_conversation(customer_id)
        
        # Add agent as participant (ignore 409 error)
        try:
            twilio_client.conversations \
                .conversations(conversation_sid) \
                .participants \
                .create(identity='agent')
        except Exception as e:
            if '409' in str(e):
                logging.info(f"Agent already in conversation {conversation_sid}")
            else:
                logging.error(f"Failed to add agent to conversation: {e}")

        # Send the message to Twilio
        send_message_to_conversation(conversation_sid, message, 'Agent')
        logging.info(f"✅ Agent message sent to conversation {conversation_sid}")

        # Generate current time once
        current_time = datetime.utcnow()
        
        # ✅ Save in DB with proper field handling
        session = Session()
        try:
            # Create the interaction with all required fields from your schema
            # IMPORTANT: Remove the created_at field since it's causing the error
            interaction = ClientInteraction(
                customer_id=customer_id,
                sender='agent',
                message_text=message,
                timestamp=current_time,
                conversation_sid=conversation_sid,
                session_id=str(uuid.uuid4()),
                is_escalated=True
            )
            
            session.add(interaction)
            session.commit()
            
            # ✅ Notify customer via Socket.IO
            socketio.emit('new_message', {
                'sender': 'agent',
                'message': message,
                'customer_id': customer_id,
                'timestamp': current_time.isoformat()
            }, room=f'customer_{customer_id}')
            return jsonify({'status': 'success', 'message': 'Message sent successfully'})
        except Exception as e:
            session.rollback()
            logging.error(f"Database error saving agent message: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500
        finally:
            session.close()
            
    except Exception as e:
        logging.error(f"Error in send_agent_message: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    # In agent_send_message
    print(f"Emitting to room: customer_{customer_id}")
    socketio.emit('new_message', {...}, room=f'customer_{customer_id}')

@app.route('/agent/update_task_status', methods=['POST'])
def update_task_status():
    data = request.json
    task_id = data.get("task_id")
    status = data.get("status")

    # Fix UUID validation - handle both Twilio Task IDs and regular UUIDs
    if not task_id:
        return jsonify({"status": "error", "message": "Missing task_id parameter"}), 400
        
    try:
        # If it's a Twilio Task ID (starts with WT), we don't need UUID validation
        if not task_id.startswith('WT'):
            # Only validate UUID format for non-Twilio task IDs
            uuid.UUID(task_id)
    except (ValueError, AttributeError):
        return jsonify({"status": "error", "message": "Invalid task_id format"}), 400

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

# Add these Socket.IO event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection to Socket.IO server"""
    logging.info(f"Client connected to Socket.IO: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection from Socket.IO server"""
    logging.info(f"Client disconnected from Socket.IO: {request.sid}")

@socketio.on('error')
def handle_socket_error(error):
    """Handle Socket.IO errors"""
    logging.error(f"Socket.IO error: {error}")

@socketio.on('connect_error')
def handle_connect_error(error):
    """Handle Socket.IO connection errors"""
    logging.error(f"Socket.IO connection error: {error}")

@socketio.on('reconnect')
def handle_reconnect():
    """Handle client reconnection"""
    logging.info(f"Client reconnected: {request.sid}")

@socketio.on('join_customer_room')
def handle_join_customer_room(data):
    customer_id = data.get('customer_id')
    if customer_id:
        room_name = f'customer_{customer_id}'
        join_room(room_name)
        print(f"Customer {customer_id} joined room: {room_name}")  # For debugging
        emit('room_joined', {'room': room_name})
@socketio.on('join_agent_room')
def handle_join_agent_room():
    join_room('agent_room')
    emit('room_joined', {'room': 'agent_room'})

@app.route('/agent/get_chat_history/<customer_id>')
def get_chat_history(customer_id):
    session_db = Session()
    try:
        messages = session_db.query(ClientInteraction).filter(
            ClientInteraction.customer_id == customer_id
        ).order_by(ClientInteraction.timestamp.asc()).all()
        result = [{
            'message': msg.message_text,
            'sender': msg.sender,
            'timestamp': msg.timestamp.isoformat() if msg.timestamp else None,
            'session_id': str(msg.session_id) if msg.session_id else None
        } for msg in messages]
        return jsonify({'status': 'success', 'messages': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        session_db.close()

@app.route('/webhook/twilio_message', methods=['POST'])
def twilio_message_webhook():
    """Webhook for new messages in a Twilio Conversation."""
    event_type = request.form.get('EventType')
    
    if event_type == 'onMessageAdded':
        conversation_sid = request.form.get('ConversationSid')
        author = request.form.get('Author')
        message_body = request.form.get('Body')
        
        # We only care about messages from the customer, not the agent or system
        # The customer's identity is their customer_id
        if author and author.startswith('CID'):
            customer_id = author
            
            # Save the customer's message to the local database
            session_db = Session()
            try:
                interaction = ClientInteraction(
                    customer_id=customer_id,
                    sender='user',
                    message_text=message_body,
                    timestamp=datetime.utcnow(),
                    conversation_sid=conversation_sid,
                    session_id=str(uuid.uuid4()), # Generate a new session_id or retrieve if needed
                    is_escalated=True
                )
                session_db.add(interaction)
                session_db.commit()
                logging.info(f"✅ Saved customer message from Twilio webhook for {customer_id}")

                # Push the new message to the agent dashboard via Socket.IO
                socketio.emit('new_message', {
                    'sender': 'user',
                    'message': message_body,
                    'customer_id': customer_id,
                    'timestamp': datetime.utcnow().isoformat()
                }, room='agent_room')

            except Exception as e:
                session_db.rollback()
                logging.error(f"❌ Error saving message from Twilio webhook: {e}")
            finally:
                session_db.close()

    return '', 200


@app.route('/agent/mark_as_resolved', methods=['POST'])
def mark_as_resolved():
    data = request.get_json()
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'status': 'error', 'message': 'Missing task_id'}), 400

    session_db = Session()
    try:
        # Try to find by Twilio TaskRouter task_id first
        rag_doc = session_db.query(RAGDocument).filter(
            (RAGDocument.task_id == task_id) | (RAGDocument.document_id == task_id)
        ).first()
        if not rag_doc:
            return jsonify({'status': 'error', 'message': 'Chat session not found'}), 404

        rag_doc.status = 'resolved'
        session_db.commit()
        return jsonify({'status': 'success', 'message': 'Chat marked as resolved'})
    except Exception as e:
        session_db.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        session_db.close()

if __name__ == "__main__":
    # Start the Flask app with Socket.IO
    socketio.run(app, debug=False,  host='localhost', port=5504)
socket = io('http://localhost:5504', {
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    timeout: 20000
});