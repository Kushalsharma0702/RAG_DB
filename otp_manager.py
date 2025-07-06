import random
from twilio.rest import Client
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE
import logging

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

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
