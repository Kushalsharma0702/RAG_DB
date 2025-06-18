# In rag_utils.py

from database import engine
from sqlalchemy import text
from datetime import datetime, date
import logging
import decimal

def fetch_data(query_type, account_id):
    with engine.connect() as conn:
        logging.info(f"Fetching {query_type} data for account_id={account_id}")
        if query_type == "balance":
            # ...existing code...
            result = conn.execute(text("SELECT balance FROM customer_account WHERE account_id=:aid"), {"aid": account_id}).fetchone()
            if result:
                return {"balance": float(result[0])}
            return None

        elif query_type == "emi":
            # Debug: Check if we have EMI records in the database
            debug_check = conn.execute(text("SELECT COUNT(*) FROM emi")).fetchone()
            logging.info(f"Total EMI records in database: {debug_check[0]}")
            
            # Get customer_id for the account first
            customer_record = conn.execute(text(
                "SELECT customer_id FROM customer_account WHERE account_id = :aid"
            ), {"aid": account_id}).fetchone()
            
            if not customer_record:
                logging.warning(f"No customer found for account_id={account_id}")
                return None
                
            customer_id = customer_record[0]
            logging.info(f"Found customer_id={customer_id} for account_id={account_id}")
            
            # Get loan records for this customer
            loan_records = conn.execute(text("""
                SELECT loan_id, principal_amount, tenure_months, status 
                FROM loan 
                WHERE customer_id = :cid
            """), {"cid": customer_id}).fetchall()
            
            if not loan_records:
                logging.warning(f"No loan records found for customer_id={customer_id}")
                return None
                
            # Get the active loan
            loan_id = None
            principal = None
            tenure = None
            
            for loan in loan_records:
                if loan[3] == 'active':  # Check if loan status is active
                    loan_id = loan[0]
                    principal = float(loan[1])
                    tenure = loan[2]
                    break
            
            if not loan_id:
                loan_id = loan_records[0][0]  # Take the first loan if no active loan
                principal = float(loan_records[0][1])
                tenure = loan_records[0][2]
            
            logging.info(f"Using loan_id={loan_id} with principal={principal} and tenure={tenure}")
            
            # Calculate monthly EMI
            monthly_emi = round(principal / tenure, 2) if tenure else 0
            
            # Get EMI records for this loan
            emi_records = conn.execute(text("""
                SELECT due_date, amount_due, status, payment_date, amount_paid
                FROM emi
                WHERE loan_id = :lid
                ORDER BY due_date ASC
            """), {"lid": loan_id}).fetchall()
            
            # Process EMI records
            next_due_date = None
            next_due_amount = None
            paid_emis = []
            current_date = date.today()
            
            for emi in emi_records:
                due_date = emi[0]
                if isinstance(due_date, datetime):
                    due_date = due_date.date()
                
                amount_due = float(emi[1])
                status = emi[2]
                payment_date = emi[3]
                amount_paid = emi[4]
                
                if payment_date and isinstance(payment_date, datetime):
                    payment_date = payment_date.date()
                
                # Add to paid EMIs list if status is 'paid'
                if status == 'paid' and payment_date:
                    paid_emis.append({
                        'date': payment_date,
                        'amount': str(amount_paid or amount_due)
                    })
                
                # Find next due EMI (due date >= today and status is 'due')
                if status == 'due' and due_date and due_date >= current_date and next_due_date is None:
                    next_due_date = due_date
                    next_due_amount = str(amount_due)
            
            # Sort paid EMIs by date (newest first) and take last 3
            recent_payments = sorted(paid_emis, key=lambda x: x['date'], reverse=True)[:3]
            
            # Debug: Log what we found
            logging.info(f"Found {len(recent_payments)} recent payments")
            logging.info(f"Next due date: {next_due_date}, amount: {next_due_amount}")
            
            # Format the result
            result = {
                "monthly_emi": str(monthly_emi),
                "recent_payments": recent_payments,
                "next_due_date": next_due_date,
                "next_due_amount": next_due_amount or "N/A"
            }
            
            return result

        elif query_type == "loan":
            # ...existing code...
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