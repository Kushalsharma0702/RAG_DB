import random, time
from twilio.rest import Client
from config import TWILIO_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
otp_store = {}

def send_otp(phone_number):
    otp = str(random.randint(100000, 999999))
    otp_store[phone_number] = {"otp": otp, "attempts": 0, "timestamp": time.time()}
    
    # Use WhatsApp-specific format
    whatsapp_number = f"whatsapp:{phone_number}"

    client.messages.create(
        from_="+12563887862",
        to=phone_number,
        body=f"Your OTP is {otp}",
    )
    return otp

def validate_otp(phone, user_otp):
    data = otp_store.get(phone)
    if not data: return False, "No OTP sent."
    if time.time() - data["timestamp"] > 300:
        return False, "OTP expired."
    if data["attempts"] >= 3:
        return False, "Max attempts exceeded."

    data["attempts"] += 1
    if data["otp"] == user_otp:
        return True, "OTP validated."
    return False, "Invalid OTP."
