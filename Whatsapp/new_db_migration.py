import psycopg2
from psycopg2 import sql
import logging
from datetime import datetime, timedelta
import uuid
from new_config import DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def run_migration():
    """
    Run database migration to fix schema issues only.
    Removed all sample data insertion and forced EMI refresh.
    """
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # Check and rename vector_embedding column if needed
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

        conn.commit()
        logging.info("Migration completed: schema updated.")

    except Exception as e:
        logging.error(f"Migration error: {e}")

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_migration()
