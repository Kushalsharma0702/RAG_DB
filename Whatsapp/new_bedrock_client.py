import boto3
import os
import json
from datetime import datetime, date, timezone # Import timezone

# Initialize the Bedrock runtime client globally in this module
bedrock_runtime = boto3.client(
    service_name='bedrock-runtime',
    region_name=os.getenv('AWS_DEFAULT_REGION', 'eu-north-1') # Use region from env var
)

def parse_chat_history(chat_history_list):
    """
    Parses the frontend chat_history (list of dicts) into Bedrock's Messages API format.
    Ensures that a "user" message is followed by an "assistant" message if the history requires it,
    as Claude models expect alternating roles.
    """
    messages = []
    for entry in chat_history_list:
        role = "user" if entry.get("sender") == "user" else "assistant"
        messages.append({"role": role, "content": [{"type": "text", "text": entry.get("content")}]})
    return messages

def invoke_claude_model(messages, model_id=None, temperature=0.7, max_tokens=1000):
    """
    Invokes a Claude model with the given messages and configuration.
    """
    if model_id is None:
        model_id = os.getenv('BEDROCK_MODEL_ID', "arn:aws:bedrock:eu-north-1:844605843483:inference-profile/anthropic.claude-3-sonnet-20240229-v1:0")

    try:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body)
        )
        response_body = json.loads(response.get('body').read())
        
        for content_block in response_body.get('content', []):
            if content_block.get('type') == 'text':
                return content_block['text'].strip()
        return "No text response from model."
    except Exception as e:
        print(f"Error invoking Claude model: {e}")
        return "Sorry, I'm having trouble connecting to the AI. Please try again later."


def generate_response(chat_history, user_message):
    """
    Generates a general conversational response using Bedrock.
    """
    # Combine current user message with chat history
    formatted_messages = parse_chat_history(chat_history)
    formatted_messages.append({"role": "user", "content": [{"type": "text", "text": user_message}]})

    return invoke_claude_model(formatted_messages)

def get_chat_summary(chat_history_list):
    """
    Summarizes the chat history using a Bedrock LLM.
    """
    if not chat_history_list:
        return "No chat history available."

    # Format chat history for the LLM
    formatted_messages = parse_chat_history(chat_history_list)
    
    # Add a prompt for summarization
    formatted_messages.append({
        "role": "user",
        "content": [{"type": "text", "text": "Please summarize the main points of this conversation concisely for an agent. Focus on the customer's core query and any unresolved issues. Max 100 words."}]
    })

    summary = invoke_claude_model(formatted_messages, max_tokens=150)
    if summary and "No text response from model" not in summary:
        return summary
    return "Could not generate a summary."

def get_embedding(text):
    """
    Gets a vector embedding for the given text using Amazon Titan Embed Text.
    """
    try:
        response = bedrock_runtime.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text})
        )
        result = json.loads(response['body'].read())
        return result["embedding"]
    except Exception as e:
        print(f"❌ Error getting embedding: {e}")
        return None

def get_intent_from_text(chat_history):
    """
    Classifies the user's intent based on chat history using a Bedrock LLM.
    Expected intents: 'emi', 'balance', 'loan', 'agent_escalation', 'unclear'.
    """
    if not chat_history:
        return "unclear"

    formatted_messages = parse_chat_history(chat_history)
    formatted_messages.append({
        "role": "user",
        "content": [{"type": "text", "text": "Based on the conversation, categorize the user's PRIMARY intent into one of these: 'emi', 'balance', 'loan', 'agent_escalation', 'unclear'. Just provide the single category word. If the user asks for human help or indicates confusion, use 'agent_escalation'."}]
    })

    try:
        response = bedrock_runtime.invoke_model(
            modelId=os.getenv('BEDROCK_MODEL_ID', "arn:aws:bedrock:eu-north-1:844605843483:inference-profile/anthropic.claude-3-sonnet-20240229-v1:0"),
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "messages": formatted_messages,
                "temperature": 0.0, # Low temperature for classification tasks
                "max_tokens": 20
            })
        )
        response_body = json.loads(response.get('body').read())
        intent = "unclear"
        for content_block in response_body.get('content', []):
            if content_block.get('type') == 'text':
                text = content_block['text'].strip().lower()
                if 'emi' in text:
                    intent = 'emi'
                elif 'balance' in text:
                    intent = 'balance'
                elif 'loan' in text:
                    intent = 'loan'
                elif 'agent' in text or 'escalation' in text:
                    intent = 'agent_escalation'
                else:
                    intent = 'unclear'
                break
        
        print(f"Classified intent: {intent} from history ending with: {chat_history[-1]['content']}")
        return intent

    except Exception as e:
        print(f"Error classifying intent with Bedrock: {e}")
        return "unclear"


def generate_data_response(query_type, data):
    """
    Formats the fetched financial data into a user-friendly string.
    """
    response_text = ""
    if query_type == "balance" and data:
        balance = data.get("balance", "N/A")
        response_text = f"Your current account balance is: ₹{balance:,.2f}"
    elif query_type == "emi" and data:
        monthly_emi = data.get("monthly_emi", "N/A")
        next_due_date = data.get("next_due_date", "N/A")
        next_due_amount = data.get("next_due_amount", "N/A")
        recent_payments = data.get("recent_payments", [])

        response_text = f"Your monthly EMI is: ₹{monthly_emi:,.2f}.\n"
        if next_due_date != "N/A" and next_due_amount != "N/A":
            response_text += f"Your next EMI of ₹{next_due_amount:,.2f} is due on {next_due_date}.\n"
        else:
            response_text += "No upcoming EMI details found.\n"

        if recent_payments:
            response_text += "\nRecent Payments:\n"
            for payment in recent_payments:
                payment_date = payment.get("date", "N/A")
                payment_amount = payment.get("amount", "N/A")
                response_text += f"- ₹{payment_amount:,.2f} on {payment_date}\n"
        else:
            response_text += "No recent EMI payments found."

    elif query_type == "loan" and data:
        loan_type = data.get("loan_type", "N/A")
        principal_amount = data.get("principal_amount", "N/A")
        interest_rate = data.get("interest_rate", "N/A")
        tenure_months = data.get("tenure_months", "N/A")

        response_text = f"Here are your loan details:\n" \
                        f"- Loan Type: {loan_type}\n" \
                        f"- Principal Amount: ₹{principal_amount:,.2f}\n" \
                        f"- Interest Rate: {interest_rate}%\n" \
                        f"- Tenure: {tenure_months} months"
    else:
        response_text = "I couldn't retrieve the specific financial information. Please try again."
    
    return response_text

