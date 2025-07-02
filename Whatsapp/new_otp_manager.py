import random, time
from twilio.rest import Client
import logging

from new_config import TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE

# Initialize Twilio client
client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# In-memory OTP storage: {phone_number: {"otp": "123456", "attempts": 0, "timestamp": 1234567890}}
# WARNING: This is IN-MEMORY storage and is NOT suitable for production environments.
# For production, consider using a persistent store like Redis or a database table
# to ensure OTPs survive application restarts and work across multiple instances.
otp_store = {}

def send_otp(phone_number: str) -> str | None:
    """
    Generates a random 6-digit OTP and sends it to the provided phone number.
    
    Args:
        phone_number (str): The phone number to send the OTP to, in E.164 format (e.g., '+1234567890')
        
    Returns:
        str: The generated OTP if successful, None otherwise
    """
    try:
        # Generate a 6-digit OTP
        otp = str(random.randint(100000, 999999))
        
        # Store OTP with timestamp and reset attempts counter
        otp_store[phone_number] = {
            "otp": otp,
            "attempts": 0,
            "timestamp": time.time()
        }
        
        # Send the OTP via Twilio
        # Ensure TWILIO_PHONE is correctly configured in your .env for sending messages
        if not TWILIO_PHONE:
            logging.error("TWILIO_PHONE is not configured in environment variables. Cannot send OTP.")
            return None

        message = client.messages.create(
            from_=TWILIO_PHONE, # Your Twilio phone number (or WhatsApp sender ID)
            to=phone_number,
            body=f"Your OTP for Financial Chatbot is {otp}. This code expires in 5 minutes."
        )
        
        logging.info(f"OTP sent to {phone_number}: {message.sid}")
        return otp
    except Exception as e:
        logging.error(f"Error sending OTP to {phone_number}: {e}")
        return None

def validate_otp(phone: str, user_otp: str) -> tuple[bool, str]:
    """
    Validates a user-provided OTP against the stored value.
    
    Args:
        phone (str): The phone number the OTP was sent to (used as key in otp_store)
        user_otp (str): The OTP provided by the user
        
    Returns:
        tuple: (bool, str) indicating success/failure and a message
    """
    data = otp_store.get(phone)
    if not data:
        logging.warning(f"No OTP found for phone {phone} during validation.")
        return False, "No OTP sent or session expired. Please try sending the OTP again."
    
    # Check if OTP has expired (5 minutes = 300 seconds)
    if time.time() - data["timestamp"] > 300:
        del otp_store[phone] # Clean up expired OTP
        logging.warning(f"OTP expired for phone {phone}.")
        return False, "OTP expired. Please request a new one."
    
    # Check if too many attempts
    # For a robust system, this attempt counter might also be persisted.
    data["attempts"] += 1
    if data["attempts"] > 3: # Allow up to 3 attempts
        logging.warning(f"Maximum validation attempts exceeded for phone {phone}. Attempts: {data['attempts']}")
        # Optionally, clear the OTP after max attempts to force new request
        del otp_store[phone] 
        return False, "Maximum validation attempts exceeded. Please request a new OTP."
    
    # Validate the OTP
    if data["otp"] == user_otp:
        del otp_store[phone] # Clean up OTP after successful validation
        logging.info(f"OTP validated successfully for phone {phone}.")
        return True, "OTP validated successfully."
    
    logging.warning(f"Invalid OTP '{user_otp}' provided for phone {phone}. Attempts: {data['attempts']}")
    return False, "Invalid OTP. Please try again."

