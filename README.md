# Financial Chatbot

A chatbot for retrieving financial information including EMI details, account balance, and loan information. The chatbot uses AWS Bedrock for natural language processing and intent classification.

## Prerequisites

- Docker and Docker Compose
- AWS account with Bedrock access
- Twilio account (for OTP and agent handoff)

## Setup

1. Clone this repository
2. Create a `.env` file based on `.env.example` and fill in your credentials
3. Build and start the containers:

```bash
docker-compose up -d
```

## Environment Variables

The application uses the following environment variables, which can be set in the `.env` file:

- AWS credentials for Bedrock
- Twilio credentials for OTP and messaging
- Database configuration
- Flask configuration

## Application Structure

- `app.py`: Main Flask application
- `bedrock_client.py`: Client for AWS Bedrock
- `database.py`: Database models and utilities
- `rag_utils.py`: Retrieval utilities for fetching financial data
- `otp_manager.py`: OTP generation and validation
- `twilio_chat.py`: Twilio integration for agent handoff
- `intent_classifier.py`: Rule-based intent classification
- `frontend/`: HTML/CSS/JS for the chatbot interface

## Features

- EMI details including monthly amount, past payments, and upcoming payments
- Account balance information
- Loan details including type, principal amount, and interest rate
- OTP-based authentication
- Agent handoff for complex queries
- Conversation summarization for agent review

## Containerization

The application is containerized using Docker, with separate containers for:
- The Flask application
- PostgreSQL database with pgvector extension for embedding storage

## Development

For local development:

1. Start the Docker Compose environment
2. Make changes to the code
3. Restart the containers to apply changes:

```bash
docker-compose restart app
```

## Troubleshooting

If you encounter issues:

1. Check the container logs:
```bash
docker-compose logs app
```

2. Ensure the database is accessible:
```bash
docker-compose exec db psql -U postgres -d financial_chatbot_db -c "SELECT 1"
```
