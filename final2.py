import os
import json
import uuid
import boto3
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime
from dotenv import load_dotenv
from twilio.rest import Client
from flask_cors import CORS
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import psycopg2
from twilio.twiml.messaging_response import MessagingResponse
import os
import re

# --- Basic Setup ---
load_dotenv()
app = Flask(__name__, template_folder=os.path.abspath('frontend'))
CORS(app)

# --- Twilio Configuration ---
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
TWILIO_TASK_ROUTER_WORKSPACE_SID = os.getenv('TWILIO_TASK_ROUTER_WORKSPACE_SID')
TWILIO_TASK_ROUTER_WORKFLOW_SID = os.getenv('TWILIO_TASK_ROUTER_WORKFLOW_SID')
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv('TWILIO_CONVERSATIONS_SERVICE_SID')
NGROK_URL = os.getenv('NGROK_URL')
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# --- AWS Bedrock Configuration ---
AWS_REGION = os.getenv('AWS_REGION', 'eu-north-1')
CLAUDE_MODEL_ID = os.getenv('CLAUDE_MODEL_ID', 'arn:aws:bedrock:eu-north-1:844605843483:inference-profile/eu.anthropic.claude-3-7-sonnet-20250219-v1:0')
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name=AWS_REGION)

# --- Database Configuration ---
DATABASE_URL = os.getenv('DATABASE_URL')
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

# --- Agent Configuration ---
AGENT_PHONE_NUMBER = "+917983394461"

# --- In-Memory Call Tasks Storage ---
# This will now store task information fetched from the database
call_tasks = {}

LANG_CONFIG = {
    '1': {'code': 'en-IN', 'name': 'English', 'voice': 'Polly.Raveena'},
    '2': {'code': 'hi-IN', 'name': 'Hindi', 'voice': 'Polly.Raveena'},
    '3': {'code': 'te-IN', 'name': 'Telugu', 'voice': 'Polly.Raveena'}
}

# --- Database Helper Functions ---
def fetch_high_risk_customers():
    """
    Fetches high-risk customers from the database who need collection calls.
    Updated to match the exact format of data in your database.
    """
    try:
        session = Session()
        
        # Diagnostic query to understand what we have
        diagnostic_query = text("""
            SELECT 
                (SELECT COUNT(*) FROM customer) as total_customers,
                (SELECT COUNT(*) FROM collectiontask) as total_tasks,
                (SELECT COUNT(*) FROM riskscore) as total_risk_scores,
                (SELECT COUNT(*) FROM collectiontask WHERE status = 'pending') as pending_tasks,
                (SELECT COUNT(*) FROM riskscore WHERE risk_segment = 'High') as high_risk_customers
        """)
        
        diagnostic = session.execute(diagnostic_query).fetchone()
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
        
        result = session.execute(query)
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
                'status': row.status,
                'current_language': '1'
            })
        
        if not customers:
            print("⚠️ No high-risk customers with pending tasks found in the database.")
            print("You may need to reset tasks to 'pending' status by using the /reset-tasks endpoint")
            print("Or access /start-campaign?reset=true to reset tasks automatically")
            print("Please check that: 1) CollectionTask has entries with status='pending'")
            print("                   2) RiskScore has entries with risk_segment in ('High', 'high', 'Critical', 'critical')")
            print("                   3) The customer_id in RiskScore matches those in CollectionTask")
        else:
            print(f"Found {len(customers)} high-risk customers with pending tasks")
            
        session.close()
        return customers
    
    except Exception as e:
        print(f"❌ Database error: {e}")
        print("Please verify your database connection and schema structure.")
        return []

def update_task_status_in_db(task_id, status):
    """
    Updates a task's status in the database.
    """
    try:
        session = Session()
        update_query = text("""
            UPDATE CollectionTask 
            SET status = :status
            WHERE task_id = :task_id
            RETURNING task_id
        """)
        
        result = session.execute(update_query, {
            'task_id': task_id,
            'status': status
        })
        
        updated = result.rowcount > 0
        session.commit()
        session.close()
        
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
        session = Session()
        query = text("""
            INSERT INTO CallOutcome 
            (task_id, outcome_type, ptp, ptp_date, notes) 
            VALUES 
            (:task_id, :outcome_type, :ptp, :ptp_date, :notes)
        """)
        
        session.execute(query, {
            'task_id': task_id, 
            'outcome_type': outcome_type, 
            'ptp': ptp, 
            'ptp_date': ptp_date,
            'notes': notes
        })
        session.commit()
        session.close()
        print(f"✅ Call outcome recorded for task {task_id}: {outcome_type}")
    except Exception as e:
        print(f"❌ Database error recording call outcome: {e}")

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

# --- Helper Functions ---
def translate_text(text, target_lang_key, target_lang_name):
    try:
        if target_lang_key == '1': return text
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
        response = bedrock_client.invoke_model(body=body, modelId=CLAUDE_MODEL_ID, accept="application/json", contentType="application/json")
        response_body = json.loads(response.get('body').read())
        translated_text = response_body.get('content', [{'text': ''}])[0].get('text', '').strip()
        print(f"Translated to {target_lang_name}: {translated_text}")
        return translated_text
    except Exception as e:
        print(f"❌ Error during translation to {target_lang_name}: {e}")
        # Return a safe, user-facing error in English as a fallback
        return "I am currently unable to provide service in the selected language."

def update_call_status_and_outcome(task_id, status, outcome_notes):
    if task_id in call_tasks:
        call_tasks[task_id].update({
            'status': status,
            'call_outcome_notes': outcome_notes,
            'timestamp': datetime.now().isoformat()
        })
        print(f"✅ Task {task_id} updated to '{status}' with outcome: {outcome_notes}.")
        
        # Update the status in the database as well
        update_task_status_in_db(task_id, status)
        
        # Record the call outcome in the database
        record_call_outcome(task_id, outcome_notes)
    else:
        print(f"⚠️ Task ID {task_id} not found for status update.")

def send_whatsapp_summary(task_id, to_number, customer_name, loan_id, emi_amount, outcome):
    """
    Sends a summary of the call outcome to the customer via WhatsApp.
    """
    if not to_number:
        print("⚠️ Customer phone number not available. Skipping WhatsApp summary.")
        return
    
    summary_message = (
        f"📱 Sounth India Finvest Bank Payment Reminder 📱\n\n"
        f"Hello {customer_name},\n\n"
        f"This is a follow-up to our recent call about your loan {loan_id}.\n"
        f"EMI Amount Due: {emi_amount}\n\n"
        f"To make your payment online, please visit: https://southindiafinvest.com/payment/\n"
        f"For assistance, call our support at 1800-123-4567\n\n"
        f"Thank you for banking with us."
    )
    try:
        message = client.messages.create(
            from_=f'whatsapp:{TWILIO_PHONE_NUMBER}',
            body=summary_message,
            to=f'whatsapp:{to_number}'
        )
        print(f"✅ WhatsApp summary sent to customer at {to_number}. SID: {message.sid}")
    except Exception as e:
        print(f"❌ Failed to send WhatsApp summary to customer: {e}")

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
        task = client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
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

# --- TwiML Routes (Unchanged) ---
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
    #if user is responding with negative feedback then this 
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

# --- Manual Trigger Endpoint (Updated to use database) ---
@app.route("/trigger-call", methods=['POST'])
def trigger_call():
    """
    Endpoint to manually trigger a call for a specific customer.
    Now creates a collection task in the database as well.
    """
    data = request.json
    to_number = data.get('to_number')
    customer_id = data.get('customer_id')
    loan_id = data.get('loan_id_full')
    
    if not to_number or not customer_id or not loan_id:
        return jsonify({"error": "Missing required fields: to_number, customer_id, or loan_id_full"}), 400

    try:
        # Create a new collection task in the database
        session = Session()
        query = text("""
            INSERT INTO CollectionTask 
            (customer_id, loan_id, scheduled_for, priority_level, assigned_to, status)
            VALUES 
            (:customer_id, :loan_id, NOW(), 1, 'AI_BOT', 'pending')
            RETURNING task_id
        """)
        
        result = session.execute(query, {
            'customer_id': customer_id,
            'loan_id': loan_id
        })
        task_id = str(result.fetchone()[0])
        session.commit()
        
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
        
        details_result = session.execute(details_query, {
            'customer_id': customer_id,
            'loan_id': loan_id
        })
        
        customer_details = details_result.fetchone()
        session.close()
        
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
    Now fetches tasks from the database and can also reset in-progress tasks to pending.
    """
    try:
        # Clear the existing call_tasks to start fresh
        call_tasks.clear()
        
        # Check if we should reset tasks (defaults to False)
        reset_tasks = request.args.get('reset', 'false').lower() == 'true'
        
        if reset_tasks:
            # Reset in-progress tasks to pending
            session = Session()
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE status IN ('in-progress', 'failed')
                RETURNING task_id
            """)
            reset_result = session.execute(reset_query)
            reset_tasks = [str(row[0]) for row in reset_result]
            session.commit()
            session.close()
            print(f"🔄 Reset {len(reset_tasks)} tasks to 'pending' status")
        
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

            call = client.calls.create(
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
            "tasks_reset": reset_tasks if reset_tasks else []
        }), 200
    except Exception as e:
        print(f"Error starting campaign: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/customers", methods=['GET'])
def get_customers():
    """
    Returns the list of customers with collection tasks for the frontend.
    Modified to handle null values and ensure proper data formatting.
    """
    try:
        session = Session()
        
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
        
        result = session.execute(query)
        customer_list = []
        
        for row in result:
            try:
                # Try to safely format each row
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
        
        session.close()
        print(f"API returned {len(customer_list)} unique customer-loan combinations")
        
        # Print first record for debugging
        if customer_list:
            print(f"Sample record: {customer_list[0]}")
        
        return jsonify(customer_list), 200
    except Exception as e:
        print(f"Error fetching customers: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/risk-scores", methods=['GET'])
def get_risk_scores():
    """
    Returns the list of risk scores from the database.
    Useful for debugging.
    """
    try:
        session = Session()
        query = text("""
            SELECT 
                customer_id, 
                risk_segment, 
                score, 
                risk_date
            FROM 
                riskscore
            ORDER BY 
                score DESC
            LIMIT 100;
        """)
        
        result = session.execute(query)
        scores = []
        
        for row in result:
            scores.append({
                'customer_id': row.customer_id,
                'risk_date': row.risk_date.isoformat() if row.risk_date else None,
                'score': row.score,
                'risk_segment': row.risk_segment,
            })
        return jsonify(scores), 200
    except Exception as e:
        print(f"Error fetching risk scores: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/reset-tasks", methods=['POST'])
def reset_tasks():
    """
    Resets tasks to 'pending' status so they can be called again.
    Fixed to handle empty request bodies and form data.
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
        
        session = Session()
        
        if task_ids:
            # Reset specific tasks
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE task_id IN :task_ids
                RETURNING task_id
            """)
            reset_result = session.execute(reset_query, {'task_ids': tuple(task_ids)})
        else:
            # Reset all non-pending tasks
            reset_query = text("""
                UPDATE CollectionTask 
                SET status = 'pending'
                WHERE status IN ('in-progress', 'failed', 'completed', 'agent_handoff')
                RETURNING task_id
            """)
            reset_result = session.execute(reset_query)
        
        reset_tasks = [str(row[0]) for row in reset_result]
        session.commit()
        session.close()
        
        print(f"🔄 Reset {len(reset_tasks)} tasks to 'pending' status")
        return jsonify({
            "message": f"Successfully reset {len(reset_tasks)} tasks to 'pending' status",
            "reset_task_ids": reset_tasks
        }), 200
        
    except Exception as e:
        print(f"❌ Error resetting tasks: {e}")
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def page_not_found(e):
    """
    Custom 404 handler that provides helpful information
    """
    return jsonify({
        "error": "Endpoint not found",
        "message": "The requested endpoint does not exist on this server.",
        "available_endpoints": {
            "GET": [
                "/start-campaign",
                "/api/customers",
                "/api/risk-scores"
            ],
            "POST": [
                "/trigger-call",
                "/voice-language-select",                "/voice-language-select-handler",
                "/voice-confirm-identity",
                "/voice-handle-identity-confirmation",
                "/voice-emi-details",
                "/voice-explain-impact",
                "/voice-offer-support",
                "/voice-handle-support-choice",
                "/voice-connect-to-agent"
            ]
        },
        "help": "Please check the endpoint URL and HTTP method."
    }), 404

@app.route('/', methods=['GET'])
def index():
    """
    Root endpoint that serves the frontend application.
    """
    try:
        return render_template('outbound.html')
    except Exception as e:
        print(f"❌ Error serving index route: {e}")
        return jsonify({
            "application": "South India Finvest Bank - Loan Collection System",
            "status": "API running",
            "endpoints": {
                "dashboard": "/api/customers",
                "campaign": "/start-campaign",
                "reset": "/reset-tasks",
                "whatsapp": "/webhook/whatsapp"
            }
        }), 500

# If you want to serve a full frontend application with static files, add this:
@app.route('/<path:path>')
def serve_static(path):
    """
    Serve static files from the 'static' directory.
    This allows the application to serve the frontend SPA properly.
    """
    try:
        return send_from_directory('static', path)
    except Exception as e:
        print(f"❌ Error serving static file {path}: {e}")
        return jsonify({"error": "File not found"}), 404

# Add these imports at the top if not already there


@app.route("/webhook/whatsapp", methods=['POST'])
def whatsapp_webhook():
    """
    Webhook for handling incoming WhatsApp messages.
    Maintains the simple format for responses as requested.
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
        session = Session()
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
        
        result = session.execute(query, {
            'phone': sender_phone[-10:]  # Use last 10 digits to match various formats
        })
        
        customer_info = result.fetchone()
        session.close()
        
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

# Keep your existing send_whatsapp_summary function exactly as it is
def send_whatsapp_summary(task_id, to_number, customer_name, loan_id, emi_amount, outcome):
    """
    Sends a summary of the call outcome to the customer via WhatsApp.
    """
    if not to_number:
        print("⚠️ Customer phone number not available. Skipping WhatsApp summary.")
        return
    
    summary_message = (
        f"📱 Sounth India Finvest Bank Payment Reminder 📱\n\n"
        f"Hello {customer_name},\n\n"
        f"This is a follow-up to our recent call about your loan {loan_id}.\n"
        f"EMI Amount Due: {emi_amount}\n\n"
        f"To make your payment online, please visit: https://southindiafinvest.com/payment/\n"
        f"For assistance, call our support at 1800-123-4567\n\n"
        f"Thank you for banking with us."
    )
    try:
        message = client.messages.create(
            from_=f'whatsapp:{TWILIO_PHONE_NUMBER}',
            body=summary_message,
            to=f'whatsapp:{to_number}'
        )
        print(f"✅ WhatsApp summary sent to customer at {to_number}. SID: {message.sid}")
    except Exception as e:
        print(f"❌ Failed to send WhatsApp summary to customer: {e}")
@app.route("/api/debug", methods=['GET'])
def debug_info():
    """
    Returns debug information about the database.
    """
    try:
        session = Session()
        
        # Get counts
        counts_query = text("""
            SELECT 
                (SELECT COUNT(*) FROM customer) as customers,
                (SELECT COUNT(*) FROM loan) as loans,
                (SELECT COUNT(*) FROM collectiontask) as tasks,
                (SELECT COUNT(DISTINCT loan_id) FROM collectiontask) as unique_loan_tasks,
                (SELECT COUNT(DISTINCT customer_id) FROM customer) as unique_customers
        """)
        
        counts = session.execute(counts_query).fetchone()
        
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
        
        sample_rows = session.execute(sample_query).fetchall()
        sample_data = []
        
        for row in sample_rows:
            sample_data.append({
                'task_id': str(row.task_id),
                'customer_id': row.customer_id,
                'loan_id': row.loan_id,
                'status': row.status,
                'created_at': str(row.created_at)
            })
        
        session.close()
        
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
@app.route("/api/debug/customers", methods=['GET'])
def debug_customers():
    """
    Debug endpoint to view raw customer data.
    """
    try:
        session = Session()
        
        # Simple query to get all customer data
        query = text("""
            SELECT 
                ct.task_id, 
                c.customer_id, 
                c.full_name AS customer_name, 
                c.phone_number AS customer_phone_number,
                l.loan_id AS loan_id_full,
                RIGHT(l.loan_id, 4) AS loan_last4,
                e.amount_due AS emi_amount,
                e.due_date,
                ct.status,
                ct.priority_level,
                rs.risk_segment
            FROM 
                collectiontask ct
            JOIN 
                customer c ON ct.customer_id = c.customer_id
            JOIN 
                loan l ON ct.loan_id = l.loan_id
            LEFT JOIN 
                emi e ON l.loan_id = e.loan_id
            LEFT JOIN 
                riskscore rs ON c.customer_id = rs.customer_id
            LIMIT 10
        """)
        
        result = session.execute(query)
        
        # Convert to a list of dictionaries
        raw_data = []
        for row in result:
            row_dict = {}
            for column, value in row._mapping.items():
                # Convert non-serializable types to strings
                if isinstance(value, (datetime, date)):
                    row_dict[column] = str(value)
                elif isinstance(value, UUID):
                    row_dict[column] = str(value)
                elif isinstance(value, Decimal):
                    row_dict[column] = float(value)
                else:
                    row_dict[column] = value
            raw_data.append(row_dict)
        
        session.close()
        
        return jsonify({
            "count": len(raw_data),
            "data": raw_data
        }), 200
    except Exception as e:
        print(f"Error in debug endpoint: {e}")
        return jsonify({"error": str(e)}), 500
if __name__ == '__main__':
    # Make sure we're running on the correct port
    app.run(host='127.0.0.1', port=5500, debug=True)