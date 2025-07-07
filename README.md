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
- **Outbound Call System:**
  - Automated calling for loan collections
  - Multi-language support
  - Call outcome tracking
- **Twilio Integration:**
  - WhatsApp messaging
  - Voice calls with TwiML
  - Conversations API
  - TaskRouter for agent handoff
- **AWS Bedrock Claude/Gemini Integration:**  
  - Intent classification, summarization, embeddings, translation

---

## Setup

### Prerequisites

- Python 3.8+
- PostgreSQL
- Redis
- Twilio Account (with WhatsApp, Voice, TaskRouter, Conversations)
- AWS Bedrock access (for Claude/Gemini)
- HTTPS access (ngrok or similar) for webhooks

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

## API Endpoints & Webhooks

### Authentication & User Interaction
| Endpoint                         | Method | Description                                                    |
|----------------------------------|--------|----------------------------------------------------------------|
| `/`                              | GET    | Serves frontend interface (chatbot or outbound based on params)|
| `/send_otp`                      | POST   | Sends OTP to customer's registered phone                       |
| `/verify_otp`                    | POST   | Validates entered OTP for authentication                       |
| `/chat`                          | POST   | Processes user messages and generates AI responses             |
| `/connect_agent`                 | POST   | Escalates chat to human agent                                  |
| `/summarize_chat`                | POST   | Legacy endpoint redirecting to /connect_agent                  |
| `/session_status`                | GET    | Returns current status of user's web session                   |
| `/cleanup_sessions`              | POST   | Removes expired sessions from Redis storage                    |

### Agent Management
| Endpoint                           | Method | Description                                                   |
|------------------------------------|--------|---------------------------------------------------------------|
| `/agent-dashboard`                 | GET    | Serves agent dashboard interface                              |
| `/agent/chat-interface`            | GET    | Serves agent chat interface                                   |
| `/agent/unresolved_sessions`       | GET    | Lists unresolved/escalated chat sessions                      |
| `/agent/get_chat_history/<cid>`    | GET    | Retrieves chat history for specific customer                  |
| `/agent/send_message`              | POST   | Sends message from agent to customer                          |
| `/agent/mark_as_resolved`          | POST   | Marks task as resolved                                        |
| `/agent/get_or_create_conversation`| GET    | Gets or creates Twilio conversation for customer              |
| `/agent/update_task_status`        | POST   | Updates status of escalated task                              |

### Outbound Call System
| Endpoint                           | Method | Description                                                   |
|------------------------------------|--------|---------------------------------------------------------------|
| `/trigger-call`                    | POST   | Manually triggers outbound call to specific customer          |
| `/start-campaign`                  | GET    | Initiates outbound calls to high-risk customers               |
| `/reset-tasks`                     | POST   | Resets task statuses to 'pending'                             |
| `/api/customers`                   | GET    | Returns list of customers with collection tasks               |
| `/api/debug`                       | GET    | Returns debug information about database state                |

### Voice Call Workflow (TwiML Routes)
| Endpoint                           | Method   | Description                                                 |
|------------------------------------|----------|-------------------------------------------------------------|
| `/voice-language-select`           | GET/POST | Offers language selection to customer                       |
| `/voice-language-select-handler`   | POST     | Processes language selection input                          |
| `/voice-confirm-identity`          | GET/POST | Verifies customer identity                                  |
| `/voice-handle-identity-confirmation`| POST   | Processes identity confirmation input                       |
| `/voice-emi-details`               | GET/POST | Provides EMI payment details to customer                    |
| `/voice-explain-impact`            | GET/POST | Explains consequences of non-payment                        |
| `/voice-offer-support`             | GET/POST | Offers support options and payment plans                    |
| `/voice-handle-support-choice`     | POST     | Processes customer's choice regarding support               |
| `/voice-connect-to-agent`          | GET/POST | Connects call to human agent                                |

### Webhooks
| Webhook                           | Method | Description                                                   |
|-----------------------------------|--------|---------------------------------------------------------------|
| `/whatsapp/webhook`               | POST   | Handles incoming WhatsApp messages for main chatbot           |
| `/webhook/whatsapp`               | POST   | Handles incoming WhatsApp messages for outbound system        |
| `/webhook/taskrouter`             | POST   | Handles Twilio TaskRouter events                              |
| `/webhook/taskrouter_assignment`  | POST   | Handles TaskRouter assignment callbacks                       |
| `/webhook/twilio_message`         | POST   | Handles Twilio Conversations message events                   |

---

## WebSocket Events (Socket.IO)

### Connection Events
- `connect`: Handles client connection to Socket.IO
- `disconnect`: Handles client disconnection from Socket.IO

### Room Management
- `join_customer_room`: Adds customer to specific room for private messaging
- `join_agent_room`: Adds agent to agent room for notifications

### Notifications
- `new_escalated_chat`: Notifies agents of newly escalated chat
- `new_message`: Notifies customers of new messages from agents
- `room_joined`: Confirms successful room joining

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

## Required Packages

```
# Core Framework
Flask==2.2.3
Flask-Cors==3.0.10
Flask-SocketIO==5.3.6
eventlet==0.35.1
gunicorn==21.2.0

# Database
SQLAlchemy==2.0.30
psycopg2-binary==2.9.9
redis==5.0.1
pgvector==0.2.4

# Twilio Integration
twilio==8.1.0

# AWS Integration
boto3==1.28.15

# Data Processing
python-dotenv==1.0.1
requests==2.32.4
pandas==2.0.1
scikit-learn==1.7.0
numpy==2.2.6

# Environment & Configuration
python-dotenv==1.0.1
```

---

## System Architecture

- **Frontend Layer:** HTML/CSS/JS with Socket.IO for real-time communication
- **API Layer:** Flask for HTTP endpoints and Socket.IO for real-time events
- **Service Layer:**
  - Chatbot services (OTP, authentication, data fetching)
  - Outbound calling services (campaign management, call flows)
  - Agent management (task assignment, chat interface)
- **Integration Layer:**
  - Twilio (Voice, WhatsApp, TaskRouter)
  - AWS Bedrock (Claude for response generation, translation)
- **Data Layer:**
  - PostgreSQL (customer data, loan information)
  - Redis (session management)

---

## License

MIT (or your license here)