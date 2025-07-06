import os
import json
import uuid
import time
import boto3
from functools import wraps
from flask import Flask, request, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from datetime import datetime
from dotenv import load_dotenv
from twilio.rest import Client

from flask_cors import CORS

# --- Basic Setup ---
load_dotenv()
app = Flask(__name__)
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
AWS_REGION = os.getenv('AWS_REGION', 'ap-south-1')  # ✅ Mumbai Region

CLAUDE_MODEL_ID = os.getenv('CLAUDE_MODEL_ID', 'anthropic.claude-3-sonnet-20240229-v1:0')
bedrock_client = boto3.client(service_name='bedrock-runtime', region_name=AWS_REGION)

# --- Agent & In-Memory Data (For Demo) ---
AGENT_PHONE_NUMBER = "+917983394461"
call_tasks = {
    '449697e5-ef8d-436f-a1f8-7374ba26c305': {'status': 'pending', 'customer_id': 'CUST999', 'customer_name': 'Aarav Sharma', 'customer_phone_number': '+917417119014', 'loan_last4': 'N999', 'emi_amount': '₹15,000', 'due_date': '15th July', 'loan_id_full': 'LN999XYZ', 'current_language': '1'},
    '935cf83e-2bc4-4b85-b4ba-657105fe67d1': {'status': 'pending', 'customer_id': 'CUST001', 'customer_name': 'Priya Singh', 'customer_phone_number': '+917417119014', 'loan_last4': 'M123', 'emi_amount': '₹5,000', 'due_date': '10th July', 'loan_id_full': 'LN123ABC', 'current_language': '1'},
    'c84ee747-906d-4c75-9a30-d6c824d741a9': {'status': 'pending', 'customer_id': 'CUST002', 'customer_name': 'Rahul Gupta', 'customer_phone_number': '+917417119014', 'loan_last4': 'K456', 'emi_amount': '₹12,000', 'due_date': '5th July', 'loan_id_full': 'LN456DEF', 'current_language': '1'},
    'd1f2e3g4-h5i6-j7k8-l9m0-n1o2p3q4r5s6': {'status': 'pending', 'customer_id': 'CUST003', 'customer_name': 'Neha Patel', 'customer_phone_number': '+917417119014', 'loan_last4': 'P789', 'emi_amount': '₹20,000', 'due_date': '20th July', 'loan_id_full': 'LN789GHI', 'current_language': '1'}
}
LANG_CONFIG = {
    '1': {'code': 'en-IN', 'name': 'English', 'voice': 'Aditi'},
    '2': {'code': 'hi-IN', 'name': 'Hindi',   'voice': 'Aditi'},
    '3': {'code': 'te-IN', 'name': 'Telugu',  'voice': 'Aditi'}
}


# --- TwiML Decorator for Task Validation ---
def require_task(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        task_id = request.values.get('task_id')
        print(f"-> Executing {f.__name__} for Task ID: {task_id}")

        if not task_id or task_id not in call_tasks:
            response = VoiceResponse()
            response.say(
                "I'm sorry, I cannot find your call information. This may be due to a system update. Goodbye.",
                voice='Raveena', language='en-IN'
            )
            response.hangup()
            return str(response)

        task_details = call_tasks[task_id]
        lang_code_key = task_details.get('current_language', '1')
        lang_info = LANG_CONFIG.get(lang_code_key, LANG_CONFIG['1'])

        return f(task_id, task_details, lang_info, *args, **kwargs)
    return decorated_function

# --- Helper Functions (Unchanged) ---
def translate_text(text, target_lang_key, target_lang_name):
    if target_lang_key == '1':
        return text

    prompt_map = {
        '2': f"Translate the following English text to Hindi. Only provide the translated text. Do not include any conversational filler. Text: '{text}'",
        '3': f"Translate the following English text to Telugu. Only provide the translated text. Do not include any conversational filler. Text: '{text}'"
    }

    prompt_text = prompt_map.get(target_lang_key)
    if not prompt_text:
        return text

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 500,
        "temperature": 0.1,
    })

    for attempt in range(4):  # Retry up to 4 times with backoff
        try:
            response = bedrock_client.invoke_model(
                body=body,
                modelId=CLAUDE_MODEL_ID,
                accept="application/json",
                contentType="application/json"
            )
            response_body = json.loads(response.get('body').read())
            translated_text = response_body.get('content', [{'text': ''}])[0].get('text', '').strip()
            print(f"Translated to {target_lang_name}: {translated_text}")
            return translated_text
        except bedrock_client.exceptions.ThrottlingException as e:
            wait = 2 ** attempt  # 1s, 2s, 4s, 8s
            print(f"⚠️ Throttled. Waiting {wait} seconds and retrying...")
            time.sleep(wait)
        except Exception as e:
            print(f"❌ Translation failed for {target_lang_name}: {e}")
            break

    return "Translation failed. Please try again later."



def update_call_status_and_outcome(task_id, status, outcome_notes):
    if task_id in call_tasks:
        call_tasks[task_id].update({
            'status': status,
            'call_outcome_notes': outcome_notes,
            'timestamp': datetime.now().isoformat()
        })
        print(f"✅ Task {task_id} updated to '{status}' with outcome: {outcome_notes}.")
    else:
        print(f"⚠️ Task ID {task_id} not found for status update.")

def send_whatsapp_summary(task_id, to_number, customer_name, loan_id, emi_amount, outcome):
    """
    Sends a summary of the call via WhatsApp. (Simulated)
    """
    print(f"--- SIMULATING WHATSAPP SUMMARY to {to_number} ---")
    print(f"Task ID: {task_id}, Customer: {customer_name}, Loan: {loan_id}, EMI: {emi_amount}, Outcome: {outcome}")
    # In a real implementation, you would use client.messages.create(...) to send a WhatsApp message.

def create_task_router_task(task_id, task_details, outcome, call_sid):
    """
    Creates a TaskRouter task for agent handoff. (Simulated)
    """
    print(f"--- SIMULATING TASKROUTER TASK CREATION ---")
    print(f"Creating TaskRouter task for Task ID: {task_id} with outcome: {outcome}")
    print(f"Customer details: {task_details.get('customer_name')}, Call SID: {call_sid}")
    # In a real implementation, you would use the TaskRouter client to create a task:
    # client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
    #     .tasks.create(
    #         attributes=json.dumps({
    #             'call_sid': call_sid,

@app.route("/voice-language-select", methods=['POST', 'GET'])
@require_task
def voice_language_select(task_id, task_details, lang_info):
    response = VoiceResponse()
    action_url = f"{NGROK_URL}/voice-language-select-handler?task_id={task_id}"
    
    gather = Gather(num_digits=1, action=action_url, method='POST')
    gather.say("Hello. Welcome to our financial assistance line. For English, press 1.", voice="Raveena", language="en-IN")
    gather.say("\u0939\u093f\u0902\u0926\u0940 \u0915\u0947 \u0932\u093f\u090f, 2 \u0926\u092c\u093e\u090f\u0902\u0964", voice="Kajal", language="hi-IN")
    gather.say("\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41 \u0c15\u0c4b\u0c38\u0c02, 3 \u0c28\u0c4a\u0c15\u0c4d\u0c15\u0c02\u0c21\u0c3f.", voice="Shruti", language="te-IN")
    response.append(gather)
    
    response.say("We did not receive your input. Goodbye.", voice="Raveena", language="en-IN")
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
    prompt_english = f"Hello, this is XYZ Bank AI calling. Am I speaking with {customer_name}?"
    prompt_translated = translate_text(prompt_english, task_details['current_language'], lang_info['name'])
    
    action_url = f"{NGROK_URL}/voice-handle-identity-confirmation?task_id={task_id}"
    gather = Gather(input="speech", timeout="5", action=action_url, method="POST")
    gather.say(prompt_translated, voice=lang_info['voice'], language=lang_info['code'])
    response.append(gather)
    
    fallback_prompt = translate_text("We did not receive a clear response. Goodbye.", task_details['current_language'], lang_info['name'])
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

    prompt1_english = f"Thank you. I’m calling about your loan ending in {task_details.get('loan_last4', 'XXXX')}, which has an outstanding EMI of {task_details.get('emi_amount', 'a certain amount')} due on {task_details.get('due_date', 'a recent date')}."
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
    response.pause(length=1)
    response.say(translate_text(prompt2_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    response.redirect(f'{NGROK_URL}/voice-offer-support?task_id={task_id}')
    return str(response)

@app.route("/voice-offer-support", methods=['POST', 'GET'])
@require_task
def voice_offer_support(task_id, task_details, lang_info):
    response = VoiceResponse()

    prompt1_english = "If you’re facing difficulties, we have options like part payments or revised EMI plans. Would you like me to guide you through these or send a payment link?"
    
    response.say(translate_text(prompt1_english, task_details['current_language'], lang_info['name']), voice=lang_info['voice'], language=lang_info['code'])
    
    confirm_assistance_english = "To protect your credit, I recommend completing the payment today. Is there anything else I can assist you with?"
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

# --- Manual Trigger Endpoint ---
@app.route("/trigger-call", methods=['POST'])
def trigger_call():
    """
    Endpoint to manually trigger a call for testing.
    """
    data = request.json
    to_number = data.get('to_number')
    if not to_number:
        return jsonify({"error": "Missing 'to_number' in request body"}), 400

    task_id = str(uuid.uuid4())
    call_tasks[task_id] = {
        'status': 'pending',
        'customer_name': data.get('customer_name', 'Valued Customer'),
        'loan_last4': data.get('loan_last4', 'XXXX'),
        'emi_amount': data.get('emi_amount', 'unknown'),
        'due_date': data.get('due_date', 'unknown'),
        'customer_phone_number': to_number,
        'customer_id': data.get('customer_id', f'CUST-{uuid.uuid4().hex[:6]}'),
        'loan_id_full': data.get('loan_id_full', f'LN-{uuid.uuid4().hex[:8]}'),
        'current_language': '1'
    }

    return jsonify({"message": "Task created. Use /start-campaign to initiate calls.", "task_id": task_id}), 200

@app.route("/start-campaign", methods=['GET'])
def start_campaign():
    """
    Initiates outbound calls for high-risk customers.
    """
    try:
        customers_to_call = [
            {'task_id': task_id, **details}
            for task_id, details in call_tasks.items()
            if details['status'] == 'pending'
        ]
        
        print(f"📞 Found {len(customers_to_call)} customers to call.")

        calls_initiated_details = []
        for customer in customers_to_call:
            task_id = customer['task_id']
            customer_phone_number = customer['customer_phone_number']
            call_tasks[task_id]['status'] = 'dialing'

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
            "calls_initiated": calls_initiated_details
        }), 200
    except Exception as e:
        print(f"Error starting campaign: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/customers", methods=['GET'])
def get_customers():
    """
    Returns the list of customers (simulated tasks) for the frontend.
    """
    customer_list = [
        {'task_id': task_id, **details} for task_id, details in call_tasks.items()
    ]
    return jsonify(customer_list), 200

if __name__ == '__main__':
    # Note: Setting debug=False is recommended if you have issues with the server auto-reloading
    # and clearing the in-memory 'call_tasks' dictionary during testing.
    app.run(host='127.0.0.1', port=5500, debug=True)
