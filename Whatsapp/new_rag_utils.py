import logging
from datetime import datetime, date, timedelta, timezone # Changed UTC to timezone
import decimal

from sqlalchemy import text
from new_database import engine, Session, CustomerAccount, Loan, EMI # Import models for ORM where beneficial

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def fetch_data(query_type: str, customer_id: str) -> dict | None:
    """
    Fetches financial data based on query type and customer_id.
    
    Args:
        query_type (str): The type of data to fetch ('balance', 'emi', 'loan').
        customer_id (str): The ID of the customer.
        
    Returns:
        dict: A dictionary containing the fetched data, or None if not found/error.
    """
    session = Session()
    try:
        logging.info(f"Fetching {query_type} data for customer_id={customer_id}")

        # First, retrieve the account_id linked to this customer_id
        customer_account_record = session.query(CustomerAccount).filter(
            CustomerAccount.customer_id == customer_id
        ).first()

        if not customer_account_record:
            logging.warning(f"No customer account found for customer_id={customer_id}. Cannot fetch financial data.")
            return None
        
        account_id = customer_account_record.account_id
        logging.info(f"Found account_id={account_id} for customer_id={customer_id}")

        if query_type == "balance":
            result = session.query(CustomerAccount.balance).filter(CustomerAccount.account_id == account_id).first()
            if result:
                # Convert Decimal to float for consistent JSON serialization in bedrock_client
                return {"balance": float(result.balance)}
            return None

        elif query_type == "emi":
            # Get loan record for this customer
            loan_record = session.query(Loan).filter(
                Loan.customer_id == customer_id,
                Loan.status == 'active' # Prioritize active loan
            ).first()

            if not loan_record:
                # Fallback to any loan if no active one
                loan_record = session.query(Loan).filter(
                    Loan.customer_id == customer_id
                ).first()
                if not loan_record:
                    logging.warning(f"No loan records found for customer_id={customer_id}.")
                    return None
                logging.info(f"No active loan found, using first available loan for customer_id={customer_id}.")
                
            loan_id = loan_record.loan_id
            principal = float(loan_record.principal_amount)
            tenure = loan_record.tenure_months
            
            logging.info(f"Using loan_id={loan_id} with principal={principal} and tenure={tenure}")
            
            # Calculate monthly EMI
            monthly_emi = round(principal / tenure, 2) if tenure else 0
            
            # Get EMI records for this loan
            emi_records = session.query(EMI).filter(
                EMI.loan_id == loan_id
            ).order_by(EMI.due_date.asc()).all()
            
            next_due_date = "N/A"
            next_due_amount = "N/A"
            paid_emis = []
            current_date = datetime.now(timezone.utc).date() # Changed UTC to timezone.utc
            
            for emi in emi_records:
                emi_due_date = emi.due_date.date() if emi.due_date else None
                
                # Collect paid EMIs
                if emi.status == 'paid' and emi.payment_date:
                    paid_emis.append({
                        'date': emi.payment_date.isoformat(), # Convert to string for consistent output
                        'amount': float(emi.amount_paid or emi.amount_due) # Ensure float
                    })
                
                # Find the next due EMI
                if emi.status == 'due' and emi_due_date and emi_due_date >= current_date:
                    if next_due_date == "N/A" or emi_due_date < datetime.fromisoformat(next_due_date).date(): # Find the earliest upcoming due date
                        next_due_date = emi_due_date.isoformat()
                        next_due_amount = float(emi.amount_due)
            
            # Sort paid EMIs by date (newest first) and take last 3
            recent_payments = sorted(paid_emis, key=lambda x: x['date'], reverse=True)[:3]
            
            result = {
                "monthly_emi": float(monthly_emi), # Ensure float
                "recent_payments": recent_payments,
                "next_due_date": next_due_date,
                "next_due_amount": next_due_amount
            }
            logging.info(f"Fetched EMI data: {result}")
            return result

        elif query_type == "loan":
            loan_record = session.query(Loan).filter(
                Loan.customer_id == customer_id,
                Loan.status == 'active' # Prioritize active loan
            ).first()

            if not loan_record:
                # Fallback to any loan if no active one
                loan_record = session.query(Loan).filter(
                    Loan.customer_id == customer_id
                ).first()
                if not loan_record:
                    logging.warning(f"No loan records found for customer_id={customer_id}.")
                    return None
            
            return {
                "loan_type": loan_record.loan_type,
                "principal_amount": float(loan_record.principal_amount), # Ensure float
                "interest_rate": float(loan_record.interest_rate), # Ensure float
                "tenure_months": loan_record.tenure_months
            }
        
        return None
    except Exception as e:
        logging.error(f"❌ Error fetching {query_type} data for customer {customer_id}: {e}")
        return None
    finally:
        session.close()

