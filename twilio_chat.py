from twilio.rest import Client
import logging
from config import TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_CONVERSATIONS_SERVICE_SID # Ensure TWILIO_CONVERSATIONS_SERVICE_SID is imported here
import json # Import json for attributes

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

def create_conversation(user_id):
    """
    Creates a new Twilio Conversation within the configured service,
    and adds the customer and a generic agent as participants.
    """
    conversation_sid = None
    conversation_unique_name = f"customer_{user_id}_handoff"
    friendly_name = f"Chat with {user_id}"

    try:
        # Try to find an existing conversation by UniqueName within the service
        try:
            conversation = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                            .conversations.get(conversation_unique_name).fetch()
            conversation_sid = conversation.sid
            logging.info(f"Existing conversation found for {user_id}: {conversation_sid}")
        except Exception as e_get:
            if "404 not found" in str(e_get).lower():
                logging.info(f"No existing conversation with unique name '{conversation_unique_name}'. Creating a new one within service {TWILIO_CONVERSATIONS_SERVICE_SID}.")
                # If not found, create a new conversation within the specified service
                conversation = client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                                .conversations.create(
                                    friendly_name=friendly_name,
                                    unique_name=conversation_unique_name, # Critical for easy lookup
                                    attributes=json.dumps({"customer_id": user_id})
                                )
                conversation_sid = conversation.sid
                logging.info(f"New conversation created for {user_id} in service {TWILIO_CONVERSATIONS_SERVICE_SID}: {conversation_sid}")
            else:
                logging.error(f"Error fetching conversation by unique name: {e_get}")
                return None

        if conversation_sid:
            # Add customer as participant to the conversation within the service
            try:
                client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                    .conversations(conversation_sid) \
                    .participants.create(identity=user_id, attributes=json.dumps({"type": "customer"}))
                logging.info(f"Customer {user_id} added as participant to conversation {conversation_sid}")
            except Exception as e_customer_add:
                if "Participant already exists" not in str(e_customer_add):
                    logging.error(f"Error adding customer {user_id} to conversation {conversation_sid}: {e_customer_add}")
                else:
                    logging.info(f"Customer {user_id} already participant in conversation {conversation_sid}")

            # Add a generic agent as participant to the conversation within the service
            try:
                agent_identity = "live_agent_1" # This identity should match your agent's identity
                client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
                    .conversations(conversation_sid) \
                    .participants.create(identity=agent_identity, attributes=json.dumps({"type": "agent"}))
                logging.info(f"Agent {agent_identity} added as participant to conversation {conversation_sid}")
            except Exception as e_agent_add:
                if "Participant already exists" not in str(e_agent_add):
                    logging.error(f"Error adding agent {agent_identity} to conversation {conversation_sid}: {e_agent_add}")
                else:
                    logging.info(f"Agent {agent_identity} already participant in conversation {conversation_sid}")
        
        return conversation_sid

    except Exception as e:
        logging.error(f"❌ Error in create_conversation (Twilio): {e}")
        return None

def send_message_to_conversation(conversation_sid, sender_id, message):
    """
    Sends a message into a Twilio Conversation within the configured service.
    """
    try:
        client.conversations.v1.services(TWILIO_CONVERSATIONS_SERVICE_SID) \
            .conversations(conversation_sid) \
            .messages.create(
                author=sender_id,
                body=message
            )
        logging.info(f"✅ Message sent to conversation {conversation_sid} by {sender_id}")
    except Exception as e:
        logging.error(f"❌ Error sending message to conversation {conversation_sid}: {e}")