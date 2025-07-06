import boto3
import os
import json
from datetime import datetime, date

# Initialize the Bedrock runtime client globally in this module
# Ensure the region_name matches your AWS Bedrock setup
bedrock_runtime = boto3.client(
    service_name='bedrock-runtime',
    region_name='eu-north-1' # Make sure this matches your Bedrock region
)

def parse_chat_history(chat_history_list):
    """
    Parses the frontend chat_history (list of dicts) into Bedrock's Messages API format.
    Ensures that a "user" message is followed by an "assistant" message if the history requires it,
    as Claude models expect alternating roles.
    """
    messages = []
    # Ensure messages alternate between 'user' and 'assistant'
    for entry in chat_history_list:
        role = "user" if entry.get("sender") == "user" else "assistant"
        # FIX: Check for 'content' from the web UI, fallback to 'message' for other sources (like WhatsApp session history)
        message_text = entry.get("content") or entry.get("message", "")
        
        # FIX: Ensure the message text is not empty before appending to avoid Bedrock API errors.
        if message_text and message_text.strip():
            # Claude 3 expects content to be a list of content blocks
            messages.append({"role": role, "content": [{"type": "text", "text": message_text}]})

    # If the last message is from the assistant and the next prompt is from the user,
    # it naturally follows. If the last message is user and we're adding another user prompt,
    # we need to be careful with Bedrock's API (it typically expects alternating).
    # For intent classification and summarization, we append a user message so it should be fine.
    return messages

def invoke_claude_model(messages, model_id="arn:aws:bedrock:eu-north-1:844605843483:inference-profile/eu.anthropic.claude-3-7-sonnet-20250219-v1:0"):
    """
    Helper function to invoke the Claude model with a given set of messages.
    """
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096, # Adjust max_tokens as needed for your responses/summaries
        "messages": messages
    }

    try:
        response = bedrock_runtime.invoke_model(
            body=json.dumps(body),
            modelId=model_id,
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response.get('body').read())

        # Claude 3 models return content as a list of content blocks
        generated_text = ""
        for content_block in response_body.get('content', []):
            if content_block.get('type') == 'text':
                generated_text += content_block['text']
        return generated_text

    except Exception as e:
        print(f"❌ Error invoking Claude model: {e}")
        raise # Re-raise the exception to be handled by the calling function

def generate_response(query_type, data, chat_history):
    """
    Generates a natural language response based on the query type and data retrieved.
    """
    messages = parse_chat_history(chat_history)
    prompt_text = ""

    serializable_data = data.copy()

    if 'recent_payments' in serializable_data and serializable_data['recent_payments']:
        serializable_data['recent_payments'] = [
            {
                "date": p['date'].strftime("%Y-%m-%d") if isinstance(p['date'], date) else p['date'],
                "amount": p['amount']
            }
            for p in serializable_data['recent_payments']
        ]

    if 'next_due_date' in serializable_data and isinstance(serializable_data['next_due_date'], date):
        serializable_data['next_due_date'] = serializable_data['next_due_date'].strftime("%Y-%m-%d")

    if query_type == "emi":
        monthly_emi = data.get("monthly_emi", "N/A")
        next_due_date = data.get("next_due_date", "N/A")
        next_due_amount = data.get("next_due_amount", "N/A")
        recent_payments = data.get("recent_payments", [])

        # Format the EMI message with more validation
        if monthly_emi and monthly_emi != "N/A":
            try:
                # Try to format as a numeric value with commas
                monthly_emi_float = float(monthly_emi)
                monthly_emi = f"{monthly_emi_float:,.2f}"
            except:
                pass  # Keep as is if not convertible
        
        if next_due_amount and next_due_amount != "N/A":
            try:
                next_due_amount_float = float(next_due_amount)
                next_due_amount = f"{next_due_amount_float:,.2f}"
            except:
                pass  # Keep as is if not convertible

        recent_payments_str = ""
        if recent_payments:
            for payment in recent_payments:
                display_date = payment.get('date')
                if isinstance(display_date, date):
                    display_date = display_date.strftime("%B %d, %Y")
                
                amount = payment.get('amount', '0')
                try:
                    amount_float = float(amount)
                    amount = f"{amount_float:,.2f}"
                except:
                    pass  # Keep as is if not convertible
                
                recent_payments_str += f"    * On **{display_date}**, you paid **₹{amount}**.\n"
        else:
            recent_payments_str = "    * No recent payment details available.\n"

        prompt_text = f"""The user has asked about their {query_type} details.
Here is the raw account information relevant to their {query_type} query:
<account_data>
{json.dumps(serializable_data, indent=2)}
</account_data>

Please provide a clear and concise response based on the provided data, specifically in the following structured format.
Prioritize clarity and emphasize monetary amounts using bold text.

Output Format:
"Thanks for verifying! Here's a breakdown of your **EMI details**:

* Your **monthly EMI amount** is **₹{monthly_emi}**.


Do you have any other questions about your EMI or loan?"

Please ensure all monetary values are bolded. Maintain the exact formatting including bullet points and line breaks.
If the next_due_date is "N/A", say "No upcoming EMI scheduled" instead.
"""
    elif query_type == "balance":
        balance = data.get("balance", "N/A")
        prompt_text = f"""The user has asked about their account balance.
Here is the raw account information:
<account_data>
{json.dumps(serializable_data, indent=2)}
</account_data>

Please provide a clear and concise response stating their current account balance. Emphasize the balance amount using bold text.
Example: "Your current account balance is **₹50,000.00**."
"""
    elif query_type == "loan":
        loan_type = data.get("loan_type", "N/A")
        principal_amount = data.get("principal_amount", "N/A")
        interest_rate = data.get("interest_rate", "N/A")

        prompt_text = f"""The user has asked about their loan details.
Here is the raw account information:
<account_data>
{json.dumps(serializable_data, indent=2)}
</account_data>

Please provide a clear and concise response summarizing their loan details. Include loan type, principal amount, and interest rate. Emphasize key figures like amounts and rates using bold text.
Example: "Your **{loan_type}** has a principal amount of **₹{principal_amount}** with an interest rate of **{interest_rate}%**."
"""
    else:
        prompt_text = f"""The user has asked about their {query_type}. Here are the details from their account:\n<account_data>\n{json.dumps(serializable_data, indent=2)}\n</account_data>\nPlease provide a helpful response based on this information.
"""

    messages.append({"role": "user", "content": [{"type": "text", "text": prompt_text}]})
    return invoke_claude_model(messages)


def get_chat_summary(chat_history):
    messages = parse_chat_history(chat_history)
    messages.append({
        "role": "user",
        "content": [{
  "type": "text",
  "text": """You are a customer support assistant summarizing conversations.

Summarize this chat with:
- **Intent**: What the user wanted (EMI details, balance, etc.)
- **User Info**: Key account details discussed
- **Bot Response**: What was shared
- **Issue**: If unresolved, why?
- **Escalation Required**: Yes/No

Format the summary as:

**Summary**
- Intent: ...
- User Info: ...
- Bot Response: ...
- Issue: ...
- Escalation Required: ...

Keep the response under 1000 tokens and easy for humans to read."""
}]
    })

    return invoke_claude_model(messages)


def get_intent_from_text(chat_history_list):
    """
    Uses Bedrock (Claude 3 Sonnet) to classify the user's intent.
    """
    # Extract the last user message from chat history
    last_user_message = ""
    for message in reversed(chat_history_list):
        if message.get("sender") == "user":
            # Correctly get the message text from the 'message' key
            last_user_message = message.get("message", "")
            break
    
    # If no user message found, return unclear
    if not last_user_message:
        return "unclear"
    
    # Format messages for the model - removing the system role and using anthropic_version instead
    messages = [
        {
            "role": "user",
            "content": [{
                "type": "text",
                "text": f"""Classify the following user message into exactly one of these categories: 'emi', 'balance', 'loan', or 'unclear'.
                
User message: "{last_user_message}"

Rules:
- EMI: If the user is asking about EMI payments, installments, payment history, or next payment date
- Balance: If the user is asking about account balance, available funds, or credit
- Loan: If the user is asking about loan amount, loan type, interest rate, or loan details
- Unclear: If the message doesn't clearly fit any of the above categories

Respond with only one word: 'emi', 'balance', 'loan', or 'unclear'."""
            }]
        }
    ]

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 10, # Max 10 tokens for a single intent word
        "temperature": 0.0, # Set to 0 for deterministic classification
        "messages": messages,
        "system": "You are an expert financial assistant that classifies user queries into specific categories." # System prompt goes here
    }

    try:
        response = bedrock_runtime.invoke_model(
            body=json.dumps(body),
            modelId="arn:aws:bedrock:eu-north-1:844605843483:inference-profile/eu.anthropic.claude-3-7-sonnet-20250219-v1:0",
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response.get('body').read())
        intent = "unclear"
        for content_block in response_body.get('content', []):
            if content_block.get('type') == 'text':
                text = content_block['text'].strip().lower()
                # Extract just the category word
                if 'emi' in text:
                    intent = 'emi'
                elif 'balance' in text:
                    intent = 'balance'
                elif 'loan' in text:
                    intent = 'loan'
                else:
                    intent = 'unclear'
                break

        print(f"Classified intent: {intent} from message: {last_user_message[:30]}...")
        return intent

    except Exception as e:
        print(f"Error classifying intent: {e}")
        return "unclear"
def get_embedding(text):
    try:
        response = bedrock_runtime.invoke_model(
            modelId="amazon.titan-embed-text-v2:0",
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text})
        )
        result = json.loads(response['body'].read())

        # ✅ Titan v2 returns flat list as "embedding"
        embedding_vector = result.get("embedding")
        if isinstance(embedding_vector, list) and len(embedding_vector) == 1024:
            return embedding_vector
        else:
            raise ValueError(f"Unexpected embedding shape or format: {type(embedding_vector)}, len={len(embedding_vector)}")

    except Exception as e:
        print(f"❌ Error generating embedding: {e}")
        return None
