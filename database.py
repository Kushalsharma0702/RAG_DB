# database.py
from sqlalchemy import create_engine, Column, String, DateTime, Text, Boolean, DECIMAL, ForeignKey, Integer, text, UUID
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import logging
import os
import uuid

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:Kushal07@localhost/financial_chatbot_db")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set.")

try:
    engine = create_engine(DATABASE_URL)
    logging.info("🏆 Database connected successfully!")
except Exception as e:
    logging.error(f"❌ Database connection failed: {e}")
    raise

Base = declarative_base()
Session = sessionmaker(bind=engine)

# --- Models ---

class Customer(Base):
    __tablename__ = 'customer'
    customer_id = Column(String(20), primary_key=True)
    full_name = Column(String(100))
    phone_number = Column(String(15))
    email = Column(String(100))
    pan_number = Column(String(10))
    aadhaar_number = Column(String(20))
    kyc_status = Column(String(20))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Loan(Base):
    __tablename__ = 'loan'
    loan_id = Column(String(20), primary_key=True)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'))
    loan_type = Column(String(30))
    principal_amount = Column(DECIMAL(12,2))
    interest_rate = Column(DECIMAL(5,2))
    tenure_months = Column(Integer)
    start_date = Column(DateTime)
    status = Column(String(20))
    ifsc_code = Column(String(20))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class EMI(Base):
    __tablename__ = 'emi'
    emi_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    loan_id = Column(String(20), ForeignKey('loan.loan_id'))
    due_date = Column(DateTime)
    amount_due = Column(DECIMAL(10,2))
    amount_paid = Column(DECIMAL(10,2))
    payment_date = Column(DateTime)
    status = Column(String(20))
    penalty_charged = Column(DECIMAL(10,2))
    created_at = Column(DateTime)

class CustomerAccount(Base):
    __tablename__ = 'customer_account'
    account_id = Column(String(20), primary_key=True)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'))
    account_type = Column(String(20))
    balance = Column(DECIMAL(12,2))
    credit_limit = Column(DECIMAL(12,2))
    status = Column(String(20))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Transaction(Base):
    __tablename__ = 'transaction'
    transaction_id = Column(String(20), primary_key=True)
    account_id = Column(String(20), ForeignKey('customer_account.account_id'))
    customer_id = Column(String(20), ForeignKey('customer.customer_id'))
    account_type = Column(String(20))
    transaction_type = Column(String(30))
    amount = Column(DECIMAL(10,2))
    transaction_date = Column(DateTime)
    description = Column(Text)

class ClientInteraction(Base):
    __tablename__ = 'client_interaction'
    interaction_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(50), nullable=False)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'))
    conversation_sid = Column(String(34))  # <-- Add this line
    timestamp = Column(DateTime, default=datetime.now)
    sender = Column(String(20), nullable=False) # 'user' or 'bot'
    message_text = Column(Text, nullable=False)
    intent = Column(String(500))
    stage = Column(Text)
    feedback_provided = Column(Boolean, default=False)
    feedback_positive = Column(Boolean)
    raw_response_data = Column(Text)
    embedding = Column(Vector(1024))
    is_escalated = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)

class RAGDocument(Base):
    __tablename__ = 'rag_document'
    # FIX: Change the data type from String(36) to UUID(as_uuid=True)
    # This ensures type consistency with other primary keys like interaction_id.
    document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(50), ForeignKey('customer.customer_id'), nullable=False)
    document_text = Column(Text, nullable=False)
    embedding = Column(Vector(1024))  # For storing OpenAI embeddings
    created_at = Column(DateTime, default=datetime.utcnow)
    task_id = Column(String(50), nullable=True)  # To store Twilio Task ID
    status = Column(String(20), default='pending')  # pending, in_process, resolved
    source = Column(String(20), default='web', nullable=False) # Add source column


def create_tables():
    try:
        Base.metadata.create_all(engine)
        logging.info("🏆 Tables created or verified successfully!")
    except Exception as e:
        logging.error(f"❌ Table creation failed: {e}")

def fetch_customer_by_account(account_id):
    session = Session()
    try:
        result = session.query(
            CustomerAccount.customer_id,
            Customer.phone_number
        ).join(Customer).filter(CustomerAccount.account_id == account_id).first()
        if result:
            logging.info(f"🏆 Customer fetched for account_id={account_id}")
            return {
                "customer_id": result.customer_id,
                "phone_number": result.phone_number
            }
        logging.warning(f"❌ No customer found for account_id={account_id}")
        return None
    except Exception as e:
        logging.error(f"❌ Error fetching customer by account: {e}")
        return None
    finally:
        session.close()

def save_chat_interaction(session_id: uuid.UUID, sender: str, message_text: str, customer_id: str = None, intent: str = None, stage: str = None, feedback_provided: bool = False, feedback_positive: bool = None, raw_response_data: dict = None, embedding: list = None):
    """
    Save a chat interaction to the database
    """
    db = Session()
    try:
        interaction = ClientInteraction(
            session_id=session_id,
            customer_id=customer_id,
            sender=sender,
            message_text=message_text,
            intent=intent,
            stage=stage,
            feedback_provided=feedback_provided,
            feedback_positive=feedback_positive,
            raw_response_data=raw_response_data,
            embedding=embedding,
            created_at=datetime.now()
        )
        db.add(interaction)
        db.commit()
        logging.info(f"Chat interaction saved for customer {customer_id}")
        return interaction.interaction_id
    except Exception as e:
        db.rollback()
        logging.error(f"Error saving chat interaction: {e}")
        return None
    finally:
        db.close()

def save_unresolved_chat(customer_id: str, summary: str, embedding: list, task_id: str, source: str = 'web'):
    """
    Saves a summarized, unresolved chat session to the RAG documents table.
    """
    db = Session()
    try:
        # Check if a document with this task_id already exists
        existing_doc = db.query(RAGDocument).filter(RAGDocument.task_id == task_id).first()
        if existing_doc:
            logging.warning(f"Document with task_id {task_id} already exists. Skipping save.")
            return

        new_document = RAGDocument(
            customer_id=customer_id,
            document_text=summary,
            embedding=embedding,
            status='pending',
            task_id=task_id,  # Save the task_id
            source=source
        )
        db.add(new_document)
        db.commit()
        logging.info(f"✅ Saved unresolved chat for customer {customer_id} with task_id {task_id} from source {source}")
    except Exception as e:
        db.rollback()
        logging.error(f"❌ Error saving unresolved chat: {e}")
    finally:
        db.close()

def get_last_three_chats(customer_id: str):
    session = Session()
    try:
        query = text("""
            SELECT interaction_id, message_text, sender, intent, timestamp
            FROM client_interaction
            WHERE customer_id = :customer_id
            ORDER BY timestamp DESC
            LIMIT 3
        """)
        result = session.execute(query, {'customer_id': customer_id}).fetchall()
        return [dict(row._mapping) for row in result]
    except Exception as e:
        logging.error(f"❌ Error fetching last three chats: {e}")
        return []
    finally:
        session.close()

if __name__ == "__main__":
    logging.info("Attempting to connect to database and create tables...")
    create_tables()