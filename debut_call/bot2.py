from flask import Flask, request, jsonify
from twilio.rest import Client
from datetime import datetime
import psycopg2
import os
from dotenv import load_dotenv
import json
import boto3 # Added for AWS Bedrock

#implementation of claude with multilingual support 
load_dotenv()
app = Flask(__name__)

# Twilio credentials
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
TWILIO_CONVERSATIONS_SERVICE_SID = os.getenv("TWILIO_CONVERSATIONS_SERVICE_SID")
TWILIO_TASK_ROUTER_WORKSPACE_SID = os.getenv("TWILIO_TASK_ROUTER_WORKSPACE_SID")
TWILIO_TASK_ROUTER_WORKFLOW_SID = os.getenv("TWILIO_TASK_ROUTER_WORKFLOW_SID")
# Use environment variable for ngrok URL or default to the current one
NGROK_URL = os.getenv("NGROK_URL", "https://c584-2401-4900-84c1-4a2a-f434-5c0c-c1bc-1055.ngrok-free.app") # Make sure this is your active ngrok URL

# AWS Bedrock Credentials
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
bedrock_client = boto3.client(
    service_name='bedrock-runtime',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

# Language and Voice Configuration
LANG_CONFIG = {
    '1': {'code': 'en', 'name': 'English', 'voice': 'Polly.Raveena', 'lang_attr': "language='en-IN'"},
    '2': {'code': 'hi', 'name': 'Hindi', 'voice': 'Polly.Raveena', 'lang_attr': "language='hi-IN'"},
    '3': {'code': 'kn', 'name': 'Kannada', 'voice': 'Polly.Raveena', 'lang_attr': "language='kn-IN'"},
    '4': {'code': 'te', 'name': 'Telugu', 'voice': 'Polly.Raveena', 'lang_attr': "language='te-IN'"}
}

# Database configuration
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

def get_db_connection():
    return psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        host=DB_HOST, port=DB_PORT
    )

def get_customer_and_loan_details(task_id):
    """Fetches customer and loan details from the database for a given task_id."""
    conn = get_db_connection()
    cur = conn.cursor()
    details = {
        "customer_name": "Valued Customer",
        "loan_last4": "XXXX",
        "emi_amount": "a certain amount",
        "due_date": "a recent date"
    }
    try:
        cur.execute("""
            SELECT c.full_name, l.loan_id, e.amount_due, e.due_date
            FROM CollectionTask ct
            JOIN Customer c ON ct.customer_id = c.customer_id
            JOIN Loan l ON ct.loan_id = l.loan_id
            LEFT JOIN Emi e ON l.loan_id = e.loan_id AND e.status = 'due'
            WHERE ct.task_id = %s
            ORDER BY e.due_date ASC
            LIMIT 1;
        """, (task_id,))
        result = cur.fetchone()
        if result:
            full_name, loan_id, emi_amount, due_date = result
            details["customer_name"] = full_name
            details["loan_last4"] = loan_id[-4:] if loan_id else "XXXX"
            details["emi_amount"] = f"₹{emi_amount:,.2f}" if emi_amount else "a certain amount"
            details["due_date"] = due_date.strftime('%dth %B') if due_date else "a recent date"
            print(f"✅ Fetched details for task {task_id}: {details}")
    except Exception as e:
        print(f"❌ DB Error fetching details for task {task_id}: {e}")
    finally:
        cur.close()
        conn.close()
    return details

def translate_text(text, target_lang_code, target_lang_name):
    """Translates text using AWS Bedrock and Claude."""
    if target_lang_code == 'en':
        return text
    try:
        prompt = f"\n\nHuman: Translate the following English text to {target_lang_name}. Only provide the translation, nothing else.\n\nText: \"{text}\"\n\nAssistant:"
        
        body = json.dumps({
            "prompt": prompt,
            "max_tokens_to_sample": 500,
            "temperature": 0.1,
        })

        response = bedrock_client.invoke_model(
            body=body,
            modelId="anthropic.claude-v2:1",
            accept="application/json",
            contentType="application/json"
        )
        
        response_body = json.loads(response.get('body').read())
        translation = response_body.get('completion', '').strip()
        print(f"Translated '{text}' to '{translation}' in {target_lang_name}")
        return translation
    except Exception as e:
        print(f"Error during translation to {target_lang_name}: {e}")
        return text # Fallback to original text on error

@app.route('/start-campaign', methods=['GET'])
def start_campaign():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Just to confirm basic data
    cur.execute("SELECT COUNT(*) FROM CollectionTask WHERE status = 'pending'")
    print("🧪 Pending tasks:", cur.fetchone())
    
    cur.execute("SELECT COUNT(*) FROM RiskScore WHERE risk_segment = 'High'")
    print("🧪 High-risk customers:", cur.fetchone())

    # Main query
    cur.execute("""
        SELECT ct.task_id, c.full_name, c.phone_number, l.loan_id
        FROM CollectionTask ct
        JOIN Customer c ON ct.customer_id = c.customer_id
        JOIN Loan l ON l.customer_id = c.customer_id
        JOIN RiskScore r ON r.customer_id = c.customer_id
        WHERE ct.status = 'pending' AND r.risk_segment = 'High'
    """)
    
    customers_to_call = cur.fetchall()
    print("📞 Customers to call:", customers_to_call)
    
    # Sanity check
    for row in customers_to_call:
        print("🔎 Row:", row)

    cur.close()
    conn.close()
    
    calls_initiated = []
    if not TWILIO_PHONE:
        print("❌ Error: TWILIO_PHONE environment variable not set.")
        return jsonify({"status": "error", "message": "TWILIO_PHONE environment variable not set."}), 500

    if not NGROK_URL:
        print("❌ Error: NGROK_URL environment variable not set.")
        return jsonify({"status": "error", "message": "NGROK_URL environment variable not set."}), 500

    for task_id, full_name, phone_number, loan_id in customers_to_call:
        try:
            # Ensure phone_number is cleaned if it has spaces
            clean_phone_number = phone_number.replace(" ", "")
            
            call = client.calls.create(
                to=clean_phone_number,
                from_=TWILIO_PHONE,
                url=f"{NGROK_URL}/voice-language-select?task_id={task_id}" # Changed to new language selection endpoint
            )
            calls_initiated.append({
                "task_id": str(task_id), # Convert UUID to string for JSON
                "phone_number": clean_phone_number,
                "call_sid": call.sid,
                "status": "initiated"
            })
            print(f"✅ Call initiated to {clean_phone_number} for Task ID {task_id}. Call SID: {call.sid}")
        except Exception as e:
            calls_initiated.append({
                "task_id": str(task_id),
                "phone_number": clean_phone_number,
                "status": "failed",
                "error": str(e)
            })
            print(f"❌ Failed to initiate call to {clean_phone_number} for Task ID {task_id}: {e}")

    return jsonify({
        "status": "success",
        "message": f"Campaign started. Attempted to call {len(customers_to_call)} customers.",
        "calls_initiated": calls_initiated
    }), 200

@app.route('/voice-language-select', methods=['GET', 'POST'])
def voice_language_select():
    print(f"voice-language-select called with args: {request.args}, form: {request.form}")
    task_id = request.args.get('task_id') or request.form.get('task_id')
    action_url = f"{NGROK_URL}/voice-prompt?task_id={task_id}"

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<Response>
    <Gather input="dtmf" timeout="5" numDigits="1" action="{action_url}" method="POST">
        <Say voice='Polly.Raveena' language='en-IN'>For English, press 1.</Say>
        <Say voice='Polly.Raveena' language='hi-IN'>हिंदी के लिए, 2 दबाएं।</Say>
        <Say voice='Polly.Raveena' language='kn-IN'>ಕನ್ನಡಕ್ಕಾಗಿ, 3 ಒತ್ತಿರಿ.</Say>
        <Say voice='Polly.Raveena' language='te-IN'>తెలుగు కోసం, 4 నొక్కండి.</Say>
    </Gather>
    <Say>We did not receive your selection. Goodbye.</Say>
    <Hangup/>
</Response>""", 200, {'Content-Type': 'application/xml'}


@app.route('/voice-prompt', methods=['GET', 'POST'])
def voice_prompt():
    task_id = request.args.get('task_id')
    # Get selected language from DTMF input, default to English ('1')
    selected_digit = request.form.get('Digits', '1')
    lang_info = LANG_CONFIG.get(selected_digit, LANG_CONFIG['1'])
    lang_code = lang_info['code']
    lang_name = lang_info['name']
    voice = lang_info['voice']
    lang_attr = lang_info['lang_attr']

    # Fetch customer details for personalization
    details = get_customer_and_loan_details(task_id)
    agent_name = "Intalks AI"
    company_name = "Your Bank"
    customer_name = details.get("customer_name", "Customer")

    # Translate prompts
    prompt1_text = f"Hello, this is {agent_name} calling on behalf of {company_name}. Am I speaking with {customer_name}?"
    prompt2_text = f"Please say yes if this is {customer_name}."
    
    prompt1_translated = translate_text(prompt1_text, lang_code, lang_name)
    prompt2_translated = translate_text(prompt2_text, lang_code, lang_name)

    # Step 1: Greet and confirm identity
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<Response>
    <Say voice='{voice}' {lang_attr}>{prompt1_translated}</Say>
    <Pause length="2"/>
    <Gather input="speech" timeout="3" action="{NGROK_URL}/voice-step2?task_id={task_id}&amp;lang={selected_digit}" method="POST">
        <Say voice='{voice}' {lang_attr}>{prompt2_translated}</Say>
    </Gather>
</Response>""", 200, {'Content-Type': 'application/xml'}

@app.route('/voice-step2', methods=['POST'])
def voice_step2():
    task_id = request.args.get('task_id')
    selected_digit = request.args.get('lang', '1')
    lang_info = LANG_CONFIG.get(selected_digit, LANG_CONFIG['1'])
    lang_code = lang_info['code']
    lang_name = lang_info['name']
    voice = lang_info['voice']
    lang_attr = lang_info['lang_attr']

    speech_result = request.form.get('SpeechResult', '').lower()
    
    # Fetch dynamic data for the prompt
    details = get_customer_and_loan_details(task_id)
    loan_last4 = details.get("loan_last4")
    emi_amount = details.get("emi_amount")
    due_date = details.get("due_date")

    # Translate prompts
    prompt1_text = f"Thank you. I’m reaching out regarding your loan account ending in {loan_last4}, which currently shows an outstanding EMI of {emi_amount} that was due on {due_date}."
    prompt2_text = "If this EMI remains unpaid, it may be reported to the credit bureau, which can affect your credit score. Continued delay may also lead to penalty charges, collection notices, or legal escalation depending on policy."
    prompt3_text = "If you would like to pay now, say pay. If you need support or options, say help."

    prompt1_translated = translate_text(prompt1_text, lang_code, lang_name)
    prompt2_translated = translate_text(prompt2_text, lang_code, lang_name)
    prompt3_translated = translate_text(prompt3_text, lang_code, lang_name)

    # Step 2: State reason and impact
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<Response>
    <Say voice='{voice}' {lang_attr}>{prompt1_translated}</Say>
    <Pause length="1"/>
    <Say voice='{voice}' {lang_attr}>{prompt2_translated}</Say>
    <Pause length="1"/>
    <Gather input="speech" timeout="5" action="{NGROK_URL}/voice-step3?task_id={task_id}&amp;lang={selected_digit}" method="POST">
        <Say voice='{voice}' {lang_attr}>{prompt3_translated}</Say>
    </Gather>
</Response>""", 200, {'Content-Type': 'application/xml'}

@app.route('/voice-step3', methods=['POST'])
def voice_step3():
    task_id = request.args.get('task_id')
    selected_digit = request.args.get('lang', '1')
    lang_info = LANG_CONFIG.get(selected_digit, LANG_CONFIG['1'])
    lang_code = lang_info['code']
    lang_name = lang_info['name']
    voice = lang_info['voice']
    lang_attr = lang_info['lang_attr']

    speech_result = request.form.get('SpeechResult', '').lower()
    payment_link = "https://pay.example.com/emi"
    upi_id = "finance@upi"

    # Keywords for pay in different languages (simple mapping)
    pay_keywords = ["pay", "पे", "ಪಾವತಿಸು", "చెల్లించు"]
    user_wants_to_pay = any(keyword in speech_result for keyword in pay_keywords)

    if user_wants_to_pay:
        # Translate prompts for payment
        prompt1_text = f"Great! You can pay your EMI using this secure link: {payment_link} or UPI ID: {upi_id}. We have also sent these details to your WhatsApp."
        prompt2_text = "To help protect your credit and avoid penalties, please complete the payment as soon as possible. Is there anything else I can assist you with?"
        prompt3_text = "Thank you for your time. Goodbye."

        prompt1_translated = translate_text(prompt1_text, lang_code, lang_name)
        prompt2_translated = translate_text(prompt2_text, lang_code, lang_name)
        prompt3_translated = translate_text(prompt3_text, lang_code, lang_name)

        return f"""<?xml version='1.0' encoding='UTF-8'?>
<Response>
    <Say voice='{voice}' {lang_attr}>{prompt1_translated}</Say>
    <Say voice='{voice}' {lang_attr}>{prompt2_translated}</Say>
    <Pause length="2"/>
    <Say voice='{voice}' {lang_attr}>{prompt3_translated}</Say>
    <Hangup/>
</Response>""", 200, {'Content-Type': 'application/xml'}
    else:
        # Translate prompts for support
        prompt1_text = "If you’re facing financial difficulties, we have options such as part payments, revised EMI plans, or payment deferrals. Would you like to speak to an agent for more details?"
        prompt2_text = "Thank you for your time. Goodbye."

        prompt1_translated = translate_text(prompt1_text, lang_code, lang_name)
        prompt2_translated = translate_text(prompt2_text, lang_code, lang_name)

        return f"""<?xml version='1.0' encoding='UTF-8'?>
<Response>
    <Say voice='{voice}' {lang_attr}>{prompt1_translated}</Say>
    <Pause length="2"/>
    <Say voice='{voice}' {lang_attr}>{prompt2_translated}</Say>
    <Hangup/>
</Response>""", 200, {'Content-Type': 'application/xml'}

@app.route('/process-recording', methods=['POST'])
def process_recording():
    task_id = request.args.get('task_id')
    recording_url = request.form.get('RecordingUrl', '')
    speech_result = "yes"  # Replace with transcription if using Speech Recognition

    conn = get_db_connection()
    cur = conn.cursor()

    if 'yes' in speech_result.lower():
        # Update the collection task status
        try:
            cur.execute("UPDATE CollectionTask SET status = %s WHERE task_id = %s", ('in-progress', task_id))
            cur.execute("""INSERT INTO CallOutcome (task_id, outcome_type, ptp, ptp_date, payment_received, notes)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (task_id, 'RPC', True, datetime.now().date(), False, 'Customer said yes'))
            conn.commit()
            print(f"Updated CollectionTask and CallOutcome for task {task_id}")
        except Exception as e:
            conn.rollback()
            print(f"❌ Error updating DB for process_recording: {e}. This may happen if task_id does not exist.")

        # Get customer details and EMI amount from EMI table
        customer_id, name, phone, loan_id, emi_amount = None, None, None, None, None
        try:
            cur.execute("""
                SELECT c.customer_id, c.full_name, c.phone_number, l.loan_id, e.amount_due
                FROM Customer c
                JOIN CollectionTask ct ON c.customer_id = ct.customer_id
                JOIN Loan l ON ct.loan_id = l.loan_id -- Join Loan table
                LEFT JOIN Emi e ON l.loan_id = e.loan_id -- Join Emi table to get amount_due
                WHERE ct.task_id = %s
                ORDER BY e.due_date ASC NULLS LAST -- Order by due date to get the earliest due EMI
                LIMIT 1
            """, (task_id,))
            result = cur.fetchone()
            if result:
                customer_id, name, phone, loan_id, emi_amount = result
                print(f"Fetched customer details from DB for task {task_id}: {name}, {phone}, EMI: {emi_amount}")
            else:
                # Fallback to hardcoded demo data if not found in DB (for client demo)
                print(f"Customer details for task {task_id} not found in DB. Using hardcoded demo data.")
                customer_id = "DEMO-CUST-84383"
                name = "Mobile Demo User"
                phone = "+918438019383" # Ensure this matches your test phone
                loan_id = "DEMO-LOAN-84383"
                emi_amount = 5500.75 # Hardcoded EMI amount
        except Exception as e:
            print(f"❌ Error fetching customer details from DB: {e}. Using hardcoded demo data.")
            customer_id = "DEMO-CUST-84383"
            name = "Mobile Demo User"
            phone = "+918438019383"
            loan_id = "DEMO-LOAN-84383"
            emi_amount = 5500.75 # Hardcoded EMI amount

        # Clean phone number format for WhatsApp
        whatsapp_phone = phone.replace(" ", "").strip()
        
        # Create a conversation for the customer or get existing one
        conversation_sid = create_or_get_conversation(customer_id)
        
        # Send WhatsApp message through Conversations API
        send_whatsapp_message(conversation_sid, whatsapp_phone, 
            f"Hi {name}, thanks for agreeing to pay your EMI of Rs. {emi_amount} for loan {loan_id}. " 
            f"Our agent will connect with you shortly to help you complete the payment process.")
        
        # Add automated follow-up messages
        send_whatsapp_message(conversation_sid, whatsapp_phone,
            "You can pay through any of these methods:\n"
            "1. UPI: finance@upi\n"
            "2. Net Banking: Visit our website\n"
            "3. Credit/Debit Card: Our agent can help you with this")
        
        # Create a task for agent in TaskRouter
        create_task_for_agent(customer_id, name, whatsapp_phone, task_id, loan_id, emi_amount, conversation_sid)
        
        # Send final confirmation message
        send_whatsapp_message(conversation_sid, whatsapp_phone,
            "I'm connecting you with an agent now. Please stand by.")
    else:
        try:
            cur.execute("UPDATE CollectionTask SET status = %s WHERE task_id = %s", ('completed', task_id))
            conn.commit()
            print(f"Marked CollectionTask {task_id} as 'completed' (Demo: No 'yes').")
        except Exception as e:
            conn.rollback()
            print(f"❌ Error updating DB for non-yes response: {e}.")

    cur.close()
    conn.close()
    
    return "<Response><Say>Thank you. We've sent you a WhatsApp message with payment details. Our agent will contact you shortly. Goodbye.</Say></Response>", 200, {'Content-Type': 'application/xml'}

def create_or_get_conversation(customer_id):
    """Create a new conversation or get existing one for the customer"""
    try:
        conversations = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
            .conversations \
            .list(limit=20)
            
        friendly_name = f"customer_{customer_id}_handoff"
        for conversation in conversations:
            if conversation.friendly_name == friendly_name:
                print(f"✅ Found existing conversation: {conversation.sid}")
                return conversation.sid
        
        conversation = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
            .conversations \
            .create(friendly_name=friendly_name)
            
        print(f"🏆 New conversation created: {conversation.sid}")
        return conversation.sid
    except Exception as e:
        print(f"❌ Error creating/getting conversation: {e}")
        return None

def send_whatsapp_message(conversation_sid, to_number, message_body):
    """Send a WhatsApp message to the customer via Conversations API"""
    try:
        if not conversation_sid:
            print("❌ No conversation SID provided")
            return False
            
        if not to_number.startswith("whatsapp:"):
            to_number = "whatsapp:" + to_number
            
        try:
            participants = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                .conversations(conversation_sid) \
                .participants \
                .list()
                
            participant_exists = False
            for p in participants:
                if hasattr(p, 'messaging_binding') and p.messaging_binding:
                    if hasattr(p.messaging_binding, 'address') and p.messaging_binding.address == to_number:
                        participant_exists = True
                        break
        except Exception as e:
            print(f"❌ Error checking participants: {e}")
            participant_exists = False
        
        if not participant_exists:
            try:
                client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                    .conversations(conversation_sid) \
                    .participants \
                    .create(
                        messaging_binding_address=to_number,
                        messaging_binding_proxy_address="whatsapp:" + TWILIO_PHONE
                    )
                print(f"✅ Added participant {to_number} to conversation {conversation_sid}")
            except Exception as e:
                print(f"❌ Error adding participant: {e}")
                
        message = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
            .conversations(conversation_sid) \
            .messages \
            .create(
                author="Bot",
                body=message_body
            )
            
        print(f"✅ Message sent to conversation {conversation_sid}")
        return True
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

def create_task_for_agent(customer_id, customer_name, phone_number, task_id, loan_id, emi_amount, conversation_sid):
    """Create a task in TaskRouter for an agent to handle"""
    try:
        task_attributes = {
            "type": "collection_call_followup",
            "conversation_sid": conversation_sid,
            "customer": {
                "customer_id": customer_id,
                "name": customer_name,
                "phone": phone_number
            },
            "loan": {
                "loan_id": loan_id,
                "emi_amount": str(emi_amount)
            },
            "task_id": str(task_id), # Ensure task_id is string for JSON
            "channel": "whatsapp"
        }
        
        task = client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
            .tasks \
            .create(
                workflow_sid=TWILIO_TASK_ROUTER_WORKFLOW_SID,
                attributes=json.dumps(task_attributes),
                task_channel_unique_name="whatsapp"
            )
            
        print(f"🏆 Task Router Task {task.sid} created for customer {customer_id}.")
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE CollectionTask SET twilio_task_sid = %s WHERE task_id = %s", 
                       (task.sid, task_id))
            conn.commit()
            print(f"Updated CollectionTask {task_id} with Twilio Task SID {task.sid}.")
        except Exception as e:
            conn.rollback()
            print(f"❌ Error updating twilio_task_sid in DB for task {task_id}: {e}")
        finally:
            cur.close()
            conn.close()
        
        return task.sid
    except Exception as e:
        print(f"❌ Error creating task: {e}")
        return None

if __name__ == '__main__':
    app.run(debug=True)