import psycopg2
from psycopg2 import sql
import logging
from datetime import datetime, timedelta
import uuid
from config import DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def run_migration():
    """Run a database migration to fix any schema issues and add sample data"""
    conn = None
    try:
        # Connect to the database
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Check if RAG_Document table has vector_embedding or embedding column
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'rag_document'
        """)
        columns = [row[0] for row in cursor.fetchall()]
        
        if 'vector_embedding' in columns and 'embedding' not in columns:
            logging.info("Renaming vector_embedding column to embedding in rag_document table...")
            cursor.execute("ALTER TABLE rag_document RENAME COLUMN vector_embedding TO embedding")
        elif 'embedding' not in columns and 'vector_embedding' not in columns:
            logging.info("Adding embedding column to rag_document table...")
            cursor.execute("ALTER TABLE rag_document ADD COLUMN embedding vector(1024)")
        
        # Check if we have any EMI records
        cursor.execute("SELECT COUNT(*) FROM emi")
        emi_count = cursor.fetchone()[0]
        
        # Force refresh EMI data for testing
        if True:  # Changed condition to always refresh EMI data
            logging.info("Refreshing EMI sample data...")
            
            # Get all loan IDs for the test account
            cursor.execute("""
                SELECT l.loan_id, l.principal_amount, l.tenure_months
                FROM loan l
                JOIN customer_account ca ON l.customer_id = ca.customer_id
                WHERE ca.account_id = 'CC11261684'
            """)
            
            loan_data = cursor.fetchall()
            
            if loan_data:
                # Process each loan
                for loan_info in loan_data:
                    loan_id = loan_info[0]
                    principal = loan_info[1]
                    tenure = loan_info[2]
                    
                    # First, clear existing EMI records for this loan
                    cursor.execute("DELETE FROM emi WHERE loan_id = %s", (loan_id,))
                    logging.info(f"Cleared existing EMI records for loan_id {loan_id}")
                    
                    # Calculate monthly EMI amount
                    monthly_emi = principal / tenure if tenure else 0
                    
                    today = datetime.now().date()
                    
                    # Create EMI records:
                    # 3 past EMIs (paid)
                    # 1 current EMI (due this month)
                    # 2 future EMIs (due in coming months)
                    
                    # 3 months ago EMI (paid)
                    three_months_ago = today - timedelta(days=90)
                    payment_date_3m = three_months_ago + timedelta(days=5)
                    emi_id_1 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, %s, %s, 'paid', 0.00, %s)
                    """, (emi_id_1, loan_id, three_months_ago, monthly_emi, monthly_emi, payment_date_3m, datetime.now()))
                    
                    # 2 months ago EMI (paid)
                    two_months_ago = today - timedelta(days=60)
                    payment_date_2m = two_months_ago + timedelta(days=2)
                    emi_id_2 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, %s, %s, 'paid', 0.00, %s)
                    """, (emi_id_2, loan_id, two_months_ago, monthly_emi, monthly_emi, payment_date_2m, datetime.now()))
                    
                    # 1 month ago EMI (paid on time)
                    one_month_ago = today - timedelta(days=30)
                    payment_date_1m = one_month_ago - timedelta(days=1)
                    emi_id_3 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, %s, %s, 'paid', 0.00, %s)
                    """, (emi_id_3, loan_id, one_month_ago, monthly_emi, monthly_emi, payment_date_1m, datetime.now()))
                    
                    # Current month EMI (due)
                    this_month = today
                    emi_id_4 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, NULL, NULL, 'due', 0.00, %s)
                    """, (emi_id_4, loan_id, this_month, monthly_emi, datetime.now()))
                    
                    # Next month EMI (due)
                    next_month = today + timedelta(days=30)
                    emi_id_5 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, NULL, NULL, 'due', 0.00, %s)
                    """, (emi_id_5, loan_id, next_month, monthly_emi, datetime.now()))
                    
                    # 2 months later EMI (due)
                    two_months_later = today + timedelta(days=60)
                    emi_id_6 = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                        VALUES (%s, %s, %s, %s, NULL, NULL, 'due', 0.00, %s)
                    """, (emi_id_6, loan_id, two_months_later, monthly_emi, datetime.now()))
                    
                    logging.info(f"Added 6 sample EMI records for loan_id {loan_id} with monthly EMI = {monthly_emi}")
            else:
                logging.warning("No loan found for account CC11261684. Cannot add sample EMI data.")
        else:
            logging.info(f"Found {emi_count} existing EMI records. No need to add sample data.")
        
        conn.commit()
        logging.info("Migration completed successfully")
        
    except Exception as e:
        if conn:
            conn.rollback()
        logging.error(f"Migration error: {e}")
        raise
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_migration()
