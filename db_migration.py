import psycopg2
from psycopg2 import sql
import logging
from config import DATABASE_URL
import uuid
from datetime import datetime, timedelta

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
        
        if emi_count == 0:
            logging.info("No EMI data found. Adding sample EMI records...")
            
            # Get a loan ID for sample EMI records
            cursor.execute("""
                SELECT loan_id 
                FROM loan 
                WHERE customer_id = (
                    SELECT customer_id 
                    FROM customer_account 
                    WHERE account_id = 'CC11261684'
                )
                LIMIT 1
            """)
            
            loan_id_result = cursor.fetchone()
            
            if loan_id_result:
                loan_id = loan_id_result[0]
                today = datetime.now().date()
                
                # Create some sample EMI records
                # 2 past EMIs (paid)
                # 1 current EMI (due this month)
                # 2 future EMIs (due in coming months)
                
                # 2 months ago EMI (paid)
                two_months_ago = today.replace(day=15) - timedelta(days=60)
                emi_id_1 = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                    VALUES (%s, %s, %s, 15000.00, 15000.00, %s, 'paid', 0.00, %s)
                """, (emi_id_1, loan_id, two_months_ago, two_months_ago, datetime.now()))
                
                # 1 month ago EMI (paid)
                one_month_ago = today.replace(day=15) - timedelta(days=30)
                emi_id_2 = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                    VALUES (%s, %s, %s, 15000.00, 15000.00, %s, 'paid', 0.00, %s)
                """, (emi_id_2, loan_id, one_month_ago, one_month_ago, datetime.now()))
                
                # Current month EMI (due)
                this_month = today.replace(day=15)
                emi_id_3 = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                    VALUES (%s, %s, %s, 15000.00, NULL, NULL, 'due', 0.00, %s)
                """, (emi_id_3, loan_id, this_month, datetime.now()))
                
                # Next month EMI (due)
                next_month = today.replace(day=15) + timedelta(days=30)
                emi_id_4 = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                    VALUES (%s, %s, %s, 15000.00, NULL, NULL, 'due', 0.00, %s)
                """, (emi_id_4, loan_id, next_month, datetime.now()))
                
                # 2 months later EMI (due)
                two_months_later = today.replace(day=15) + timedelta(days=60)
                emi_id_5 = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO emi (emi_id, loan_id, due_date, amount_due, amount_paid, payment_date, status, penalty_charged, created_at) 
                    VALUES (%s, %s, %s, 15000.00, NULL, NULL, 'due', 0.00, %s)
                """, (emi_id_5, loan_id, two_months_later, datetime.now()))
                
                logging.info(f"Added 5 sample EMI records for loan_id {loan_id}")
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
