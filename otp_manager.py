import random, time
from twilio.rest import Client
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE
import logging

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
# In-memory OTP storage: {phone_number: {"otp": "123456", "attempts": 0, "timestamp": 1234567890}}
otp_store = {}

def send_otp(phone_number):
    """
    Generates a random 6-digit OTP and sends it to the provided phone number.
    
    Args:
        phone_number (str): The phone number to send the OTP to, in E.164 format
        
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
        message = client.messages.create(
            from_=TWILIO_PHONE,
            to=phone_number,
            body=f"Your OTP for Financial Chatbot is {otp}. This code expires in 5 minutes."
        )
        
        logging.info(f"OTP sent to {phone_number}: {message.sid}")
        return otp
    except Exception as e:
        logging.error(f"Error sending OTP: {e}")
        return None

def validate_otp(phone, user_otp):
    """
    Validates a user-provided OTP against the stored value.
    
    Args:
        phone (str): The phone number the OTP was sent to
        user_otp (str): The OTP provided by the user
        
    Returns:
        tuple: (bool, str) indicating success/failure and a message
    """
    # Check if we have an OTP for this phone number
    data = otp_store.get(phone)
    if not data:
        return False, "No OTP sent or session expired. Please try sending the OTP again."
    
    # Check if OTP has expired (5 minutes = 300 seconds)
    if time.time() - data["timestamp"] > 300:
        # Clean up expired OTP
        del otp_store[phone]
        return False, "OTP expired. Please request a new one."
    
    # Check if too many attempts
    if data["attempts"] >= 3:
        return False, "Maximum validation attempts exceeded. Please request a new OTP."
    
    # Increment attempt counter
    data["attempts"] += 1
    
    # Validate the OTP
    if data["otp"] == user_otp:
        # Clean up OTP after successful validation
        del otp_store[phone]
        return True, "OTP validated successfully."
    
    return False, "Invalid OTP. Please try again."
