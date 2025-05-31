from flask import Flask, request, jsonify, send_from_directory
import os
from otp_manager import send_otp, validate_otp
from database import fetch_customer_by_account
from rag_utils import fetch_data
from gemini_client import generate_response
from flask_cors import CORS

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)

# 🔧 Fix: Register root route properly
@app.route('/')
def serve_frontend():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static_files(path):
    return send_from_directory(app.static_folder, path)

@app.route("/send_otp", methods=["POST"])
def otp_request():
    account_id = request.json.get("account_id")
    user = fetch_customer_by_account(account_id)
    if not user:
        return jsonify({"status": "error", "message": "Account not found"}), 404
    send_otp(user.phone_number)
    return jsonify({"status": "success", "message": "OTP sent", "phone": user.phone_number})

@app.route("/verify_otp", methods=["POST"])
def otp_verify():
    phone = request.json.get("phone")
    otp = request.json.get("otp")
    is_valid, msg = validate_otp(phone, otp)
    return jsonify({"status": "success" if is_valid else "error", "message": msg})

@app.route("/query", methods=["POST"])
def answer_query():
    query_type = request.json.get("query_type")
    account_id = request.json.get("account_id")
    data = fetch_data(query_type, account_id)
    if not data:
        return jsonify({"status": "error", "message": "No data found"}), 404
    response = generate_response(query_type, data)
    return jsonify({"status": "success", "reply": response})

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    app.run(host="0.0.0.0", port=5504, debug=True)
