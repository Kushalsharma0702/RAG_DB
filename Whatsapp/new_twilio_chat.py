import logging
import json
import uuid
from datetime import datetime, timezone # Changed UTC to timezone

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from new_config import (
    TWILIO_SID, 
    TWILIO_AUTH_TOKEN, 
    TWILIO_CONVERSATIONS_SERVICE_SID,
    TWILIO_TASK_ROUTER_WORKFLOW_SID,
    TWILIO_TASK_ROUTER_WORKSPACE_SID,
    TWILIO_PHONE # This should be your Twilio WhatsApp sender ID if used for proxy_address
)

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# Custom JSON encoder to handle UUID and datetime objects for TaskRouter attributes
class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return json.JSONEncoder.default(self, obj)

def create_conversation(customer_id: str) -> str | None:
    """
    Creates a new Twilio Conversation or retrieves an existing one based on a unique name.
    """
    conversation_unique_name = f"chat_customer_{customer_id}_handoff"
    friendly_name = f"Chat with {customer_id}"

    try:
        # Try to find an existing conversation by its unique name
        conversation = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                        .conversations.get(conversation_unique_name).fetch()
        logging.info(f"Existing conversation found for {customer_id}: {conversation.sid}")
        return conversation.sid
    except TwilioRestException as e:
        if e.status == 404:
            logging.info(f"No existing conversation with unique name '{conversation_unique_name}'. Creating a new one.")
            try:
                # If not found, create a new conversation within the specified service
                conversation = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                                .conversations.create(
                                    friendly_name=friendly_name,
                                    unique_name=conversation_unique_name,
                                    attributes=json.dumps({"customer_id": customer_id})
                                )
                logging.info(f"New conversation created for {customer_id} in service {TWILIO_CONVERSATIONS_SERVICE_SID}: {conversation.sid}")
                return conversation.sid
            except TwilioRestException as create_e:
                logging.error(f"Error creating new conversation for {customer_id}: {create_e}")
                return None
        else:
            logging.error(f"Error fetching conversation by unique name: {e}")
            return None
    except Exception as e:
        logging.error(f"An unexpected error occurred in create_conversation for {customer_id}: {e}")
        return None

def add_customer_participant_to_conversation(conversation_sid: str, customer_id: str):
    """
    Adds a customer participant (using their customer_id as identity) to a Twilio Conversation.
    """
    try:
        # Check if participant already exists to avoid 409 conflict
        participants = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                        .conversations(conversation_sid) \
                        .participants.list(identity=customer_id)

        if not participants:
            participant = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                            .conversations(conversation_sid) \
                            .participants.create(identity=customer_id)
            logging.info(f"Customer participant {customer_id} added to conversation {conversation_sid}: {participant.sid}")
            return True
        else:
            logging.info(f"Customer participant {customer_id} already exists in conversation {conversation_sid}.")
            return True # Already exists, consider it successful
    except TwilioRestException as e:
        logging.error(f"Error adding customer participant {customer_id} to conversation {conversation_sid}: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred adding customer participant {customer_id}: {e}")
        return False

def add_whatsapp_participant_to_conversation(conversation_sid: str, whatsapp_number: str):
    """
    Adds a WhatsApp participant to a Twilio Conversation.
    `whatsapp_number` should be in E.164 format (e.g., '+1234567890').
    Requires TWILIO_PHONE to be set as the WhatsApp-enabled Twilio number.
    """
    try:
        # Check if participant already exists to avoid 409 conflict
        # Twilio's messaging_binding_address for WhatsApp includes 'whatsapp:' prefix.
        address_check = f"whatsapp:{whatsapp_number}"
        participants = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                        .conversations(conversation_sid) \
                        .participants.list(address=address_check)

        if not participants:
            # Ensure TWILIO_PHONE is set and is your WhatsApp-enabled number
            if not TWILIO_PHONE or not TWILIO_PHONE.startswith('whatsapp:'): # Basic check
                logging.error("TWILIO_PHONE is not configured as a WhatsApp sender ID. Cannot add WhatsApp participant.")
                return False

            participant = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                            .conversations(conversation_sid) \
                            .participants.create(
                                messaging_binding_address=address_check,
                                messaging_binding_proxy_address=TWILIO_PHONE, # Your Twilio WhatsApp sender ID
                                attributes=json.dumps({"channel_type": "whatsapp"})
                            )
            logging.info(f"WhatsApp participant {whatsapp_number} added to conversation {conversation_sid}: {participant.sid}")
            return True
        else:
            logging.info(f"WhatsApp participant {whatsapp_number} already exists in conversation {conversation_sid}.")
            return True # Already exists, consider it successful
    except TwilioRestException as e:
        logging.error(f"Error adding WhatsApp participant {whatsapp_number} to conversation {conversation_sid}: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred adding WhatsApp participant {whatsapp_number}: {e}")
        return False

def send_message_to_conversation(conversation_sid: str, author: str, message_body: str) -> bool:
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
        return True
    except TwilioRestException as e:
        logging.error(f"❌ Twilio API error sending message to conversation {conversation_sid}: {e}")
        return False
    except Exception as e:
        logging.error(f"❌ An unexpected error occurred sending message to conversation {conversation_sid}: {e}")
        return False

def create_task_for_handoff(customer_id: str, session_id: str, summary: str, customer_phone_number: str, conversation_sid: str, channel_type: str) -> str | None:
    """
    Creates a Twilio Task Router Task for agent handoff.
    """
    try:
        if not all([TWILIO_TASK_ROUTER_WORKSPACE_SID, TWILIO_TASK_ROUTER_WORKFLOW_SID]):
            logging.error("Twilio Task Router SIDs are not configured. Cannot create task.")
            return None

        task_attributes = {
            "customer_id": customer_id,
            "session_id": session_id,
            "summary": summary,
            "customer_phone_number": customer_phone_number,
            "conversation_sid": conversation_sid,
            "channel_type": channel_type, # "web" or "whatsapp"
            "type": "chat_handoff",
            "task_creation_time": datetime.now(timezone.utc).isoformat() # Changed UTC to timezone.utc
        }

        task = client.taskrouter.v1.workspaces(TWILIO_TASK_ROUTER_WORKSPACE_SID) \
                    .tasks.create(
                        workflow_sid=TWILIO_TASK_ROUTER_WORKFLOW_SID,
                        attributes=json.dumps(task_attributes, cls=CustomJsonEncoder),
                        task_channel="chat" # Assuming a 'chat' Task Channel exists
                    )
        
        logging.info(f"🏆 Task created for customer {customer_id} (Session: {session_id}, Channel: {channel_type}): {task.sid}")
        return task.sid
    except TwilioRestException as e:
        logging.error(f"❌ Twilio API error creating Task Router task for customer {customer_id}: {e}")
        return None
    except Exception as e:
        logging.error(f"❌ An unexpected error occurred creating Task Router task for customer {customer_id}: {e}")
        return None

