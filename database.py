from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

def fetch_customer_by_account(account_id):
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT c.phone_number, c.customer_id FROM CustomerAccount a
            JOIN Customer c ON a.customer_id = c.customer_id
            WHERE a.account_id = :acc_id
        """), {"acc_id": account_id}).fetchone()
        return result
