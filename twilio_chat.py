from twilio.rest import Client
import logging
import json # Import json for attributes
import uuid
import datetime # Import datetime
from config import (
    TWILIO_ACCOUNT_SID, 
    TWILIO_AUTH_TOKEN, 
    TWILIO_CONVERSATIONS_SERVICE_SID,
    TWILIO_TASK_ROUTER_WORKFLOW_SID,
    TWILIO_TASK_ROUTER_WORKSPACE_SID
)

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Custom JSON encoder to handle UUID and datetime objects
class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            # Convert UUID to string
            return str(obj)
        if isinstance(obj, datetime.datetime):
            # Convert datetime to ISO 8601 string
            return obj.isoformat()
        return json.JSONEncoder.default(self, obj)

def create_conversation(user_id):
    """
    Creates or fetches a Twilio Conversation for a given user_id.
    """
    conversation_unique_name = f"customer_{user_id}_handoff"
    friendly_name = f"Chat with {user_id}"

    try:
        # First, try to fetch the conversation by its unique name
        conversation = client.conversations.v1.conversations(conversation_unique_name).fetch()
        logging.info(f"Existing conversation found for {user_id}: {conversation.sid}")
        return conversation.sid
    except Exception as e:
        if "not found" in str(e).lower():
            logging.info(f"No existing conversation for {user_id}. Creating a new one.")
            # If not found, create a new conversation
            try:
                new_conversation = client.conversations.v1.conversations.create(
                    friendly_name=friendly_name,
                    unique_name=conversation_unique_name,
                    attributes=json.dumps({"customer_id": user_id})
                )
                logging.info(f"New conversation created for {user_id}: {new_conversation.sid}")
                return new_conversation.sid
            except Exception as e_create:
                logging.error(f"❌ Error creating new conversation: {e_create}")
                return None
        else:
            logging.error(f"❌ Error fetching conversation: {e}")
            return None

def send_message_to_conversation(conversation_sid, author, message_body):
    """
    Sends a message into a Twilio Conversation within the configured service.
    """
    try:
        client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
            .conversations(conversation_sid) \
            .messages.create(
                author=author,
                body=message_body
            )
        logging.info(f"✅ Message sent to conversation {conversation_sid} by {author}")
    except Exception as e:
        logging.error(f"❌ Error sending message to conversation {conversation_sid}: {e}")
        return None # Explicitly return None on error for consistency

def create_task_for_handoff(customer_id, phone_number, summary, recent_messages, conversation_sid):
    """
    Creates a Twilio Task Router Task for agent handoff.
    """
    try:
        # Prepare task attributes
        task_attributes = {
            "customer_id": customer_id,
            "phone_number": phone_number,
            "summary": summary,
            "recent_messages": recent_messages,
            "conversation_sid": conversation_sid,
            "type": "chat_handoff",
            "task_creation_time": datetime.datetime.now(), # Example of a datetime object
            # Add any other skills or metadata needed for routing, e.g.:
            # "required_skills": ["chat_support", "financial_expert"],
            # "priority": 10
        }

        # Use the custom encoder when dumping to JSON
        task = client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
            .tasks.create(
                workflow_sid=TWILIO_TASK_ROUTER_WORKFLOW_SID,
                attributes=json.dumps(task_attributes, cls=CustomJsonEncoder), # Use the updated custom encoder
                task_channel='chat' # Specify the task channel
            )
        
        logging.info(f"🏆 Task created for customer {customer_id}: {task.sid}")
        return task.sid
    except Exception as e:
        logging.error(f"❌ Error creating Task Router task for customer {customer_id}: {e}")
        return None

def create_and_send_to_agent(customer_id, phone_number, summary, recent_messages):
    """
    This function's role changes when using Task Router. It primarily ensures
    the conversation exists and sends initial context messages.
    The actual 'sending to agent' (i.e., agent participant addition)
    is now handled by Task Router.
    """
    conversation_sid = create_conversation(customer_id)
    
    if not conversation_sid:
        logging.error(f"Failed to create conversation for customer {customer_id}")
        return None
    
    # Send the summary
    send_message_to_conversation(
        conversation_sid,
        "System",
        f"Customer {customer_id} (Phone: {phone_number}) has requested assistance:\n\nSummary:\n{summary}"
    )
    
    # Send recent messages
    send_message_to_conversation(conversation_sid, "System", "--- Recent Messages ---")
    for msg in recent_messages:
        author = msg.get('sender', 'Unknown')
        content = msg.get('content', '(No content)')
        send_message_to_conversation(conversation_sid, author, content)
    
    send_message_to_conversation(
        conversation_sid,
        "System",
        "--- End of Context ---\nPlease assist this customer with their inquiry."
    )
    
    # This function now simply sets up the conversation and sends initial context.
    # The actual task creation for agent routing happens in app.py's
    # summarize_chat_route or connect_agent endpoint.
    return conversation_sid