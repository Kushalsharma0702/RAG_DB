import uuid
import logging
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, DateTime, Text, Boolean, DECIMAL, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Load DATABASE_URL from new_config.py
from new_config import DATABASE_URL

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set in new_config.py.")

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
    phone_number = Column(String(15), unique=True, nullable=False)
    email = Column(String(100))
    pan_number = Column(String(10))
    aadhaar_number = Column(String(20))
    # address = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

class CustomerAccount(Base):
    __tablename__ = 'customer_account'
    account_id = Column(String(20), primary_key=True)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'), nullable=False)
    account_type = Column(String(50))
    balance = Column(DECIMAL(15, 2))
    status = Column(String(20))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

class Loan(Base):
    __tablename__ = 'loan'
    loan_id = Column(String(20), primary_key=True)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'), nullable=False)
    loan_type = Column(String(50))
    principal_amount = Column(DECIMAL(15, 2))
    interest_rate = Column(DECIMAL(5, 2))
    tenure_months = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    status = Column(String(20))
    ifsc_code = Column(String(20)) # Added this from your schema provided earlier
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

class EMI(Base):
    __tablename__ = 'emi'
    emi_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    loan_id = Column(String(20), ForeignKey('loan.loan_id'), nullable=False)
    due_date = Column(DateTime)
    amount_due = Column(DECIMAL(10, 2))
    amount_paid = Column(DECIMAL(10, 2), nullable=True)
    payment_date = Column(DateTime, nullable=True)
    status = Column(String(20)) # 'due', 'paid', 'overdue'
    penalty_charged = Column(DECIMAL(10, 2), default=0.00)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

class ChatInteraction(Base):
    __tablename__ = 'client_interaction'
    interaction_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'), nullable=True)
    session_id = Column(String(50), nullable=False) # Ensure this is String
    conversation_sid = Column(String(34), nullable=True)
    message_text = Column(Text, nullable=False)
    sender = Column(String(20), nullable=False)
    intent = Column(String(50), nullable=True)
    is_escalated = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

class RAGDocument(Base):
    __tablename__ = 'rag_document'
    document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'), nullable=True)
    document_text = Column(Text, nullable=False)
    embedding = Column(Vector(1024), nullable=True) # Changed from 1536 based on common Bedrock embeddings
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    status = Column(String(20), default='pending')
    task_sid = Column(String(34), nullable=True, unique=True)

    __table_args__ = {'extend_existing': True}

class Transaction(Base):
    __tablename__ = 'transaction'
    transaction_id = Column(String(20), primary_key=True)
    customer_id = Column(String(20), ForeignKey('customer.customer_id'), nullable=False)
    account_id = Column(String(20), ForeignKey('customeraccount.account_id'), nullable=False)
    transaction_type = Column(String(50))
    amount = Column(DECIMAL(15, 2))
    transaction_date = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    description = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = {'extend_existing': True}

# --- Database Operations ---
def create_tables():
    """
    Creates all defined tables in the database if they do not already exist.
    This is safer for production usage where data loss must be avoided.
    """
    logging.info("Attempting to create database tables (without dropping)...")
    try:
        with engine.connect() as connection:
            Base.metadata.create_all(connection.engine)
        logging.info("Tables created successfully (without dropping existing data).")
    except Exception as e:
        logging.error(f"❌ Error creating tables: {e}")
        raise


def fetch_customer_by_account_id(account_id: str):
    """Fetches customer_id and phone_number based on a given account_id."""
    session = Session()
    try:
        result = session.query(Customer.customer_id, Customer.phone_number) \
                        .join(CustomerAccount, Customer.customer_id == CustomerAccount.customer_id) \
                        .filter(CustomerAccount.account_id == account_id) \
                        .first()
        if result:
            logging.info(f"Customer and phone found for account ID {account_id}.")
            return {'customer_id': result.customer_id, 'phone_number': result.phone_number}
        else:
            logging.warning(f"No customer or phone number found for account ID {account_id}.")
            return None
    except Exception as e:
        logging.error(f"❌ Error fetching customer and phone by account ID: {e}")
        return None
    finally:
        session.close()

def get_or_create_customer_by_phone(phone_number: str):
    """
    Fetches an existing customer by phone number, or creates a new one if not found.
    Returns the customer_id.
    """
    session = Session()
    try:
        customer = session.query(Customer).filter_by(phone_number=phone_number).first()
        if customer:
            logging.info(f"Existing customer found for phone {phone_number}: {customer.customer_id}")
            return customer.customer_id
        else:
            new_customer_id = f"CUST-{str(uuid.uuid4())[:8].upper()}"
            new_customer = Customer(
                customer_id=new_customer_id,
                phone_number=phone_number,
                full_name=f"WhatsApp User {new_customer_id}",
                # Add default values for other non-nullable columns if necessary, e.g.,
                email="default@example.com",
                pan_number="ABCDE1234F",
                aadhaar_number="123456789012",
                address="Unknown"
            )
            session.add(new_customer)
            session.commit()
            logging.info(f"New customer created for phone {phone_number}: {new_customer_id}")
            return new_customer_id
    except IntegrityError as e:
        session.rollback()
        # This could happen if another process/request creates the same customer
        # before this one commits. Try fetching again.
        logging.warning(f"Integrity error (possible race condition) for phone {phone_number}. Retrying fetch.")
        return get_or_create_customer_by_phone(phone_number)
    except Exception as e:
        session.rollback()
        logging.error(f"❌ Error getting or creating customer by phone: {e}")
        return None
    finally:
        session.close()

def save_chat_interaction(session_id: str, message_text: str, sender: str, intent: str = None, customer_id: str = None, conversation_sid: str = None, is_escalated: bool = False):
    """
    Saves a chat interaction to the database.
    """
    session = Session()
    try:
        chat_interaction = ChatInteraction(
            interaction_id=uuid.uuid4(), # Explicitly generate UUID here
            session_id=session_id, # This is correctly a string
            customer_id=customer_id,
            conversation_sid=conversation_sid,
            message_text=message_text,
            sender=sender,
            intent=intent,
            is_escalated=is_escalated,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(chat_interaction)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        logging.error(f"❌ Error saving chat interaction: {e}")
        return False
    finally:
        session.close()

def save_unresolved_chat(customer_id: str, summary: str, embedding: list, task_sid: str = None, status: str = 'pending'):
    session = Session()
    try:
        rag_doc = RAGDocument(
            document_id=uuid.uuid4(), # Explicitly generate UUID here
            customer_id=customer_id,
            document_text=summary,
            embedding=embedding,
            created_at=datetime.now(timezone.utc),
            status=status,
            task_sid=task_sid
        )
        session.add(rag_doc)
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        logging.warning(f"RAGDocument with task_sid {task_sid} already exists or unique constraint violation. Skipping save.")
        return False
    except Exception as e:
        session.rollback()
        logging.error(f"❌ Error saving unresolved chat summary: {e}")
        return False
    finally:
        session.close()

def get_last_three_chats(customer_id: str):
    """Fetches the last three chat interactions for a given customer."""
    session = Session()
    try:
        chats = session.query(ChatInteraction).filter(
            ChatInteraction.customer_id == customer_id
        ).order_by(ChatInteraction.timestamp.desc()).limit(3).all()
        return sorted([
            {'message_text': chat.message_text, 'sender': chat.sender, 'timestamp': chat.timestamp.isoformat()}
            for chat in chats
        ], key=lambda x: x['timestamp'])
    except Exception as e:
        logging.error(f"❌ Error fetching last three chats for customer {customer_id}: {e}")
        return []
    finally:
        session.close()

def get_all_chats_for_session(customer_id: str, session_id: str):
    """Fetches all chat interactions for a given customer_id and session_id."""
    session = Session()
    try:
        chats = session.query(ChatInteraction).filter(
            ChatInteraction.customer_id == customer_id,
            ChatInteraction.session_id == session_id
        ).order_by(ChatInteraction.timestamp.asc()).all()
        
        return [{
            'interaction_id': str(chat.interaction_id),
            'message_text': chat.message_text,
            'sender': chat.sender,
            'intent': chat.intent,
            'timestamp': chat.timestamp.isoformat(),
            'is_escalated': chat.is_escalated,
            'session_id': chat.session_id,
            'conversation_sid': chat.conversation_sid
        } for chat in chats]
    except Exception as e:
        logging.error(f"❌ Error fetching all chats for session {session_id}: {e}")
        return []
    finally:
        session.close()

def get_customer_by_id(customer_id: str):
    """Fetches a customer record by customer_id."""
    session = Session()
    try:
        customer = session.query(Customer).filter_by(customer_id=customer_id).first()
        return customer
    except Exception as e:
        logging.error(f"❌ Error fetching customer by ID {customer_id}: {e}")
        return None
    finally:
        session.close()

def update_rag_document_status(task_sid: str, status: str):
    """Updates the status of a RAGDocument linked to a TaskRouter Task."""
    session = Session()
    try:
        doc = session.query(RAGDocument).filter_by(task_sid=task_sid).first()
        if doc:
            doc.status = status
            session.commit()
            logging.info(f"Updated RAGDocument {doc.document_id} status to {status} for Task {task_sid}.")
            return True
        logging.warning(f"No RAGDocument found for Task SID {task_sid}.")
        return False
    except Exception as e:
        session.rollback()
        logging.error(f"❌ Error updating RAGDocument status for Task {task_sid}: {e}")
        return False
    finally:
        session.close()

if __name__ == "__main__":
    logging.info("Attempting to connect to database and create tables...")
    create_tables()
    logging.info("Database initialization complete.")
