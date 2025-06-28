from database import Session, engine
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def alter_rag_document_table():
    """Add task_id and status columns to the RAG document table"""
    session = None
    try:
        # Create a database connection
        session = Session()
        
        # Check database type - PostgreSQL uses the DO syntax, SQLite doesn't
        is_postgres = 'postgresql' in str(engine.url).lower()
        
        if is_postgres:
            # PostgreSQL version
            session.execute(text("""
                DO $$
                BEGIN
                    -- Add task_id column if it doesn't exist
                    IF NOT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'rag_document' AND column_name = 'task_id'
                    ) THEN
                        ALTER TABLE rag_document ADD COLUMN task_id VARCHAR(50);
                    END IF;
                    
                    -- Add status column if it doesn't exist
                    IF NOT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'rag_document' AND column_name = 'status'
                    ) THEN
                        ALTER TABLE rag_document ADD COLUMN status VARCHAR(20) DEFAULT 'pending';
                    END IF;
                END $$;
            """))
        else:
            # SQLite version (or other databases)
            # SQLite doesn't support IF NOT EXISTS for ADD COLUMN, but we can use PRAGMA
            # to check if columns exist
            
            # Check if task_id exists
            columns = session.execute(text("PRAGMA table_info(rag_document)")).fetchall()
            column_names = [col[1] for col in columns]  # Column name is at index 1
            
            # Add columns if they don't exist
            if 'task_id' not in column_names:
                session.execute(text("ALTER TABLE rag_document ADD COLUMN task_id VARCHAR(50)"))
                logger.info("Added task_id column to rag_document table")
            
            if 'status' not in column_names:
                session.execute(text("ALTER TABLE rag_document ADD COLUMN status VARCHAR(20) DEFAULT 'pending'"))
                logger.info("Added status column to rag_document table")
        
        session.commit()
        print("RAG document table altered successfully!")
        return True
    except Exception as e:
        print(f"Error altering RAG document table: {e}")
        if session:
            session.rollback()
        return False
    finally:
        if session:
            session.close()

# Run the function if this script is executed directly
if __name__ == "__main__":
    alter_rag_document_table()