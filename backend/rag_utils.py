from database import engine
from sqlalchemy import text

def fetch_data(query_type, account_id):
    with engine.connect() as conn:
        if query_type == "balance":
            result = conn.execute(text("SELECT balance FROM CustomerAccount WHERE account_id=:aid"), {"aid": account_id}).fetchone()
            return {"balance": float(result[0])} if result else None
        elif query_type == "emi":
            result = conn.execute(text("""
                SELECT due_date, amount_due FROM EMI
                WHERE loan_id IN (SELECT loan_id FROM Loan WHERE customer_id = (
                    SELECT customer_id FROM CustomerAccount WHERE account_id = :aid
                )) ORDER BY due_date
            """), {"aid": account_id}).fetchall()
            return [dict(r._mapping) for r in result]
        elif query_type == "loan":
            result = conn.execute(text("""
                SELECT loan_type, principal_amount, interest_rate FROM Loan
                WHERE customer_id = (SELECT customer_id FROM CustomerAccount WHERE account_id = :aid)
            """), {"aid": account_id}).fetchall()
            return [dict(r._mapping) for r in result]
