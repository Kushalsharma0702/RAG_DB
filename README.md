# Financial Chatbot

A full-stack financial assistant chatbot with WhatsApp, web, and agent dashboard interfaces.  
Supports EMI, account balance, loan queries, OTP authentication, escalation to live agents, and Twilio TaskRouter integration.

---

## Features

- **User Chatbot (Web & WhatsApp):**
  - EMI, balance, loan queries
  - OTP-based authentication
  - Feedback and escalation to live agent
- **Agent Dashboard:**
  - View unresolved/escalated chats
  - Real-time chat with customers
  - Mark chats as resolved
- **Twilio Integration:**
  - WhatsApp messaging
  - Conversations API
  - TaskRouter for agent handoff
- **AWS Bedrock Claude/Gemini Integration:**  
  - Intent classification, summarization, embeddings

---

## Setup

### Prerequisites

- Python 3.8+
- PostgreSQL
- Twilio Account (with WhatsApp sandbox, TaskRouter, Conversations)
- AWS Bedrock access (for Claude/Gemini)

### Installation

1. **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/financial_chatbot.git
    cd financial_chatbot
    ```

2. **Create a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

3. **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4. **Configure environment:**
    - Copy `.env.example` to `.env` and fill in your credentials.

5. **Run database migration:**
    ```bash
    python db_migration.py
    ```

6. **Start the application:**
    ```bash
    python app.py
    ```

---

## Endpoints & Webhooks

| Endpoint / Webhook                | Method | Description / Feature                                                                                      |
|------------------------------------|--------|------------------------------------------------------------------------------------------------------------|
| `/`                               | GET    | Serve main chatbot web interface                                                                           |
| `/send_otp`                       | POST   | Send OTP to user (for authentication)                                                                      |
| `/verify_otp`                     | POST   | Verify OTP entered by user                                                                                 |
| `/chat`                           | POST   | Main chat endpoint (handles user queries, intent, data fetch, escalation)                                  |
| `/summarize_chat`                 | POST   | Summarize chat and escalate to agent (used for feedback/escalation)                                        |
| `/connect_agent`                  | POST   | Escalate chat to live agent (manual connect from web UI)                                                   |
| `/agent-dashboard`                | GET    | Serve agent dashboard interface                                                                            |
| `/agent/unresolved_sessions`       | GET    | List unresolved/escalated chat sessions for agents                                                         |
| `/agent/get_chat_history/<cid>`   | GET    | Get chat history for a customer                                                                            |
| `/agent/send_message`             | POST   | Agent sends message to customer                                                                            |
| `/agent/mark_as_resolved`         | POST   | Mark a chat/task as resolved                                                                               |
| `/agent/update_task_status`        | POST   | Update status of a task (resolved/in-progress)                                                             |
| `/agent/get_last_chats`           | POST   | Get last three chat messages for a customer                                                                |
| `/agent-chat-interface`           | GET    | Serve agent chat interface (alternative UI)                                                                |
| `/ozonetel_voice`                 | POST   | IVR/voice integration endpoint                                                                             |
| `/whatsapp/webhook`               | POST   | WhatsApp webhook for Twilio (handles WhatsApp chat flow, escalation, feedback)                             |
| `/webhook/taskrouter`             | POST   | Twilio TaskRouter event webhook (task created, assigned, completed, etc.)                                  |
| `/webhook/taskrouter_assignment`  | POST   | TaskRouter assignment callback                                                                             |
| `/webhook/twilio_message`         | POST   | Twilio Conversations message webhook                                                                       |
| `/create_taskrouter_test_task`    | POST   | Create a test TaskRouter task (for testing integration)                                                    |

---

## Project Structure

```
financial_chatbot/
├── app.py                  # Main Flask app (all endpoints)
├── app_socketio.py         # Socket.IO event handlers
├── twilio_chat.py          # Twilio Conversations/TaskRouter helpers
├── bedrock_client.py       # AWS Bedrock Claude/Gemini helpers
├── rag_utils.py            # Data fetch and RAG logic
├── otp_manager.py          # OTP send/validate logic
├── intent_classifier.py    # Rule-based intent classifier
├── database.py             # SQLAlchemy models and DB helpers
├── db_migration.py         # Database migration/sample data
├── alter_rag_document.py   # DB schema migration for RAGDocument
├── config.py               # Loads environment variables
├── requirements.txt        # Python dependencies
├── .env                    # Environment variables (not committed)
├── frontend/               # Web and agent dashboard UIs (HTML/JS/CSS)
└── README.md               # This file
```

---

## Environment Variables

See `.env` for all required configuration (Twilio, AWS, DB, etc).

---

## Notes

- **WhatsApp escalation:** When a user gives negative feedback (👎), the chat is escalated to a live agent and routed via Twilio TaskRouter. The user receives a message:  
  `"Your chat has been escalated for further assistance from a live agent. They will contact you soon."`
- **Agent dashboard:** Receives real-time escalations via Socket.IO and can chat with customers.

---

## License

MIT (or your license here)