from flask import Flask, request, jsonify
from twilio.rest import Client
from datetime import datetime
import psycopg2
import os
from dotenv import load_dotenv
import json
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
NGROK_URL = os.getenv("NGROK_URL", "https://8a00-2401-4900-a60c-dcf4-b437-497-dc22-205c.ngrok-free.app") # Make sure this is your active ngrok URL

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

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
                url=f"{NGROK_URL}/voice-prompt?task_id={task_id}"
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

@app.route('/voice-prompt', methods=['GET', 'POST'])
def voice_prompt():
    task_id = request.args.get('task_id')
    return f"""<?xml version='1.0' encoding='UTF-8'?>
    <Response>
        <Say voice='alice'>Hello from Intalks AI. If you want to pay your loan EMI, say yes after the beep.</Say>
        <Record timeout='5' maxLength='5' playBeep='true' action="{NGROK_URL}/process-recording?task_id={task_id}" />
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