from flask import Flask, request, jsonify
from twilio.rest import Client
from datetime import datetime
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Twilio credentials
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE")
AGENT_PHONE = os.getenv("AGENT_PHONE")  # Add this to your .env
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
    cur.execute("""
        SELECT ct.task_id, c.full_name, c.phone_number, l.loan_id
        FROM CollectionTask ct
        JOIN Customer c ON ct.customer_id = c.customer_id
        JOIN Loan l ON l.customer_id = c.customer_id
        JOIN RiskScore r ON r.customer_id = c.customer_id
        WHERE ct.status = 'pending' AND r.risk_segment = 'High'
    """)
    customers = cur.fetchall()
    print("📞 Customers:", customers)
    for task_id, name, phone, loan_id in customers:
        try:
            call = client.calls.create(
                url=f"https://dfa0-2401-4900-86a5-27fe-af5a-8c1c-94ed-5e7a.ngrok-free.app/voice-prompt?task_id={task_id}",
                to=phone,
                from_=TWILIO_PHONE
            )
            print("✅ Call initiated:", call.sid)
        except Exception as e:
            print("❌ Call failed:", e)
    cur.close()
    conn.close()
    return jsonify({"status": "calls initiated"})

@app.route('/voice-prompt', methods=['GET', 'POST'])
def voice_prompt():
    task_id = request.args.get('task_id')
    return f"""<?xml version='1.0' encoding='UTF-8'?>
    <Response>
        <Say voice='alice'>Hello from Intalks AI. If you want to pay your loan EMI, say yes after the beep.</Say>
        <Record timeout='5' maxLength='5' playBeep='true' action="/process-recording?task_id={task_id}" />
    </Response>""", 200, {'Content-Type': 'application/xml'}

@app.route('/process-recording', methods=['POST'])
def process_recording():
    task_id = request.args.get('task_id')
    recording_url = request.form['RecordingUrl']
    speech_result = "yes"  # Replace with transcription if using Speech Recognition

    conn = get_db_connection()
    cur = conn.cursor()

    if 'yes' in speech_result.lower():
        cur.execute("UPDATE CollectionTask SET status = %s WHERE task_id = %s", ('in-progress', task_id))
        cur.execute("""INSERT INTO CallOutcome (task_id, outcome_type, ptp, ptp_date, payment_received, notes)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (task_id, 'RPC', True, datetime.now().date(), False, 'Customer said yes'))

        cur.execute("""SELECT c.full_name, c.phone_number FROM Customer c
                       JOIN CollectionTask ct ON c.customer_id = ct.customer_id
                       WHERE ct.task_id = %s""", (task_id,))
        name, phone = cur.fetchone()

        # Send WhatsApp message
        client.messages.create(
            from_='whatsapp:' + TWILIO_PHONE,
            to='whatsapp:' + phone.replace(" ", "").strip(),
            body=f"Hi {name}, you agreed to pay your EMI. Our agent will reach out to you shortly."
        )

        # Call the agent and connect them to the customer
        client.calls.create(
            to=AGENT_PHONE,
            from_=TWILIO_PHONE,
            url=f"https://dfa0-2401-4900-86a5-27fe-af5a-8c1c-94ed-5e7a.ngrok-free.app/connect-agent?task_id={task_id}"
        )
    else:
        cur.execute("UPDATE CollectionTask SET status = %s WHERE task_id = %s", ('completed', task_id))

    conn.commit()
    cur.close()
    conn.close()
    return "<Response><Say>Thank you. Goodbye.</Say></Response>", 200, {'Content-Type': 'application/xml'}

@app.route('/connect-agent', methods=['POST'])
def connect_agent():
    return """<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>Connecting you to the customer who showed interest.</Say>
        <Dial><Client>customer</Client></Dial>
    </Response>""", 200, {'Content-Type': 'application/xml'}

if __name__ == '__main__':
    app.run(debug=True)
