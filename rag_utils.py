# In rag_utils.py

from database import engine
from sqlalchemy import text
from datetime import datetime, date # Import date for type checking
import logging

def fetch_data(query_type, account_id):
    with engine.connect() as conn:
        logging.info(f"Fetching {query_type} data for account_id={account_id}")
        if query_type == "balance":
            result = conn.execute(text("SELECT balance FROM customer_account WHERE account_id=:aid"), {"aid": account_id}).fetchone()
            if result:
                return {"balance": float(result[0])}
            return None

        elif query_type == "emi":
            # Improved EMI query with better columns and sorting
            emi_records = conn.execute(text("""
                SELECT
                    e.due_date,
                    e.amount_due,
                    e.status,
                    e.payment_date,
                    e.amount_paid,
                    l.principal_amount,
                    l.tenure_months,
                    l.principal_amount / l.tenure_months AS calculated_monthly_emi
                FROM emi e
                JOIN loan l ON e.loan_id = l.loan_id
                WHERE l.customer_id = (SELECT customer_id FROM customer_account WHERE account_id = :aid)
                ORDER BY e.due_date ASC
            """), {"aid": account_id}).fetchall()

            if not emi_records:
                logging.warning(f"No EMI records found for account_id={account_id}")
                return None

            emi_list_raw = [dict(r._mapping) for r in emi_records]
            
            # Log the raw data for debugging
            logging.info(f"Raw EMI data found: {len(emi_list_raw)} records")

            # Calculate monthly EMI - get it from the calculated field
            monthly_emi = None
            if emi_list_raw and 'calculated_monthly_emi' in emi_list_raw[0]:
                monthly_emi = str(round(float(emi_list_raw[0]['calculated_monthly_emi']), 2))
            
            next_due_date = None
            next_due_amount = None
            recent_payments = []
            current_date = date.today()
            
            # Process all EMI records
            for emi in emi_list_raw:
                # Convert dates safely
                due_date = emi.get('due_date')
                if isinstance(due_date, datetime):
                    due_date = due_date.date()
                
                payment_date = emi.get('payment_date')
                if isinstance(payment_date, datetime):
                    payment_date = payment_date.date()
                
                # Add paid EMIs to recent payments
                if emi.get('status') == 'paid' and payment_date is not None:
                    amount_paid = emi.get('amount_paid')
                    if amount_paid is None:
                        amount_paid = emi.get('amount_due', 0)
                    
                    recent_payments.append({
                        "date": payment_date,
                        "amount": str(amount_paid)
                    })
                
                # Find the next due EMI
                if emi.get('status') == 'due' and due_date is not None:
                    if due_date >= current_date and next_due_date is None:
                        next_due_date = due_date
                        next_due_amount = str(emi.get('amount_due', 0))

            # Sort recent payments by date (newest first) and take top 3
            recent_payments = sorted(recent_payments, key=lambda x: x['date'], reverse=True)[:3]

            # Format the next due date nicely if it exists
            formatted_next_due_date = None
            if next_due_date:
                try:
                    formatted_next_due_date = next_due_date.strftime("%Y-%m-%d")
                except:
                    formatted_next_due_date = str(next_due_date)

            # Build and return the result
            result = {
                "monthly_emi": monthly_emi or "N/A",
                "recent_payments": recent_payments,
                "next_due_date": formatted_next_due_date or "N/A",
                "next_due_amount": next_due_amount or "N/A"
            }
            
            logging.info(f"Processed EMI data: {result}")
            return result

        elif query_type == "loan":
            result = conn.execute(text("""
                SELECT loan_type, principal_amount, interest_rate FROM loan
                WHERE customer_id = (SELECT customer_id FROM customer_account WHERE account_id = :aid)
            """), {"aid": account_id}).fetchall()

            if result:
                loan_data = dict(result[0]._mapping)
                return {
                    "loan_type": loan_data.get("loan_type"),
                    "principal_amount": str(loan_data.get("principal_amount")),
                    "interest_rate": str(loan_data.get("interest_rate"))
                }
            return None

        return None