import redis
import json
import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from config import REDIS_HOST, REDIS_PORT, REDIS_DB

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionManager:
    """
    Centralized session management for both web and WhatsApp interactions
    """
    
    def __init__(self, redis_host=REDIS_HOST, redis_port=REDIS_PORT, redis_db=REDIS_DB):
        try:
            self.redis_client = redis.StrictRedis(
                host=redis_host, 
                port=redis_port, 
                db=redis_db, 
                decode_responses=True
            )
            # Test connection
            self.redis_client.ping()
            logger.info("✅ Redis connection established successfully")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            raise
            
        self.session_timeout = 1200  # 20 minutes in seconds
        self.otp_timeout = 300       # 5 minutes for OTP validation
        
    def create_session(self, user_identifier: str, channel: str = 'web') -> str:
        """
        Create a new session for a user
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            channel: 'web' or 'whatsapp'
            
        Returns:
            session_id: Unique session identifier
        """
        session_id = str(uuid.uuid4())
        session_key = f"session:{channel}:{user_identifier}"
        
        session_data = {
            'session_id': session_id,
            'user_identifier': user_identifier,
            'channel': channel,
            'stage': 'greeting' if channel == 'whatsapp' else 'initial',
            'created_at': datetime.now().isoformat(),
            'last_activity': datetime.now().isoformat(),
            'authenticated': False,
            'customer_id': None,
            'account_id': None,
            'phone_number': None,
            'otp': None,
            'otp_attempts': 0,
            'otp_created_at': None,
            'intent': None,
            'conversation_history': [],
            'escalated': False,
            'escalation_reason': None
        }
        
        try:
            # Store session with expiration
            self.redis_client.setex(
                session_key, 
                self.session_timeout, 
                json.dumps(session_data)
            )
            
            logger.info(f"Created new session {session_id} for {user_identifier} on {channel}")
            return session_id
        except Exception as e:
            logger.error(f"❌ Failed to create session: {e}")
            raise
    
    def get_session(self, user_identifier: str, channel: str = 'web') -> Optional[Dict]:
        """
        Retrieve session data
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            channel: 'web' or 'whatsapp'
            
        Returns:
            session_data: Dict containing session information or None
        """
        session_key = f"session:{channel}:{user_identifier}"
        try:
            session_data = self.redis_client.get(session_key)
            
            if session_data:
                try:
                    return json.loads(session_data)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode session data for {user_identifier}")
                    return None
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get session: {e}")
            return None
    
    def _clean_data_for_json(self, data):
        """Clean data to ensure it's JSON serializable"""
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if callable(value):
                    continue  # Skip callable values
                elif hasattr(value, 'isoformat'):  # datetime objects
                    cleaned[key] = value.isoformat()
                elif isinstance(value, (list, tuple)):
                    cleaned[key] = [self._clean_data_for_json(item) for item in value]
                elif isinstance(value, dict):
                    cleaned[key] = self._clean_data_for_json(value)
                else:
                    cleaned[key] = value
            return cleaned
        elif isinstance(data, (list, tuple)):
            return [self._clean_data_for_json(item) for item in data]
        else:
            return data

    def update_session(self, user_identifier: str, updates: Dict, channel: str = 'web') -> bool:
        """
        Update session data
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            updates: Dict of fields to update
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: Success status
        """
        session_key = f"session:{channel}:{user_identifier}"
        session_data = self.get_session(user_identifier, channel)
        
        if not session_data:
            logger.warning(f"Session not found for {user_identifier} on {channel}")
            return False
        
        try:
            # Clean updates for JSON serialization
            cleaned_updates = self._clean_data_for_json(updates)
            
            # Update fields
            session_data.update(cleaned_updates)
            session_data['last_activity'] = datetime.now().isoformat()
            
            # Reset expiration
            self.redis_client.setex(
                session_key, 
                self.session_timeout, 
                json.dumps(session_data)
            )
            
            logger.info(f"Updated session for {user_identifier} on {channel}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to update session: {e}")
            return False
    
    def set_otp(self, user_identifier: str, otp: str, channel: str = 'web') -> bool:
        """
        Set OTP for a session
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            otp: OTP string
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: Success status
        """
        updates = {
            'otp': otp,
            'otp_attempts': 0,
            'otp_created_at': datetime.now().isoformat()
        }
        return self.update_session(user_identifier, updates, channel)
    
    def validate_otp(self, user_identifier: str, user_otp: str, channel: str = 'web') -> tuple:
        """
        Validate OTP for a session
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            user_otp: OTP provided by user
            channel: 'web' or 'whatsapp'
            
        Returns:
            tuple: (is_valid: bool, message: str, should_regenerate: bool)
        """
        session_data = self.get_session(user_identifier, channel)
        
        if not session_data:
            return False, "Session not found. Please restart the process.", True
        
        stored_otp = session_data.get('otp')
        otp_created_at = session_data.get('otp_created_at')
        otp_attempts = session_data.get('otp_attempts', 0)
        
        if not stored_otp or not otp_created_at:
            return False, "No OTP found. Please request a new OTP.", True
        
        # Check OTP expiration
        otp_time = datetime.fromisoformat(otp_created_at)
        if datetime.now() - otp_time > timedelta(seconds=self.otp_timeout):
            self.update_session(user_identifier, {'otp': None, 'otp_created_at': None}, channel)
            return False, "OTP has expired. Please request a new OTP.", True
        
        # Check attempt limit
        if otp_attempts >= 3:
            self.update_session(user_identifier, {'otp': None, 'otp_created_at': None}, channel)
            return False, "Maximum OTP attempts exceeded. Please request a new OTP.", True
        
        # Increment attempts
        self.update_session(user_identifier, {'otp_attempts': otp_attempts + 1}, channel)
        
        # Validate OTP
        if stored_otp == user_otp:
            # Mark as authenticated and clear OTP
            self.update_session(user_identifier, {
                'authenticated': True,
                'otp': None,
                'otp_created_at': None,
                'otp_attempts': 0
            }, channel)
            return True, "OTP validated successfully.", False
        else:
            return False, f"Invalid OTP. {2 - otp_attempts} attempts remaining.", False
    
    def is_session_expired(self, user_identifier: str, channel: str = 'web') -> bool:
        """
        Check if session is expired
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: True if expired or not found
        """
        session_data = self.get_session(user_identifier, channel)
        return session_data is None
    
    def add_to_conversation_history(self, user_identifier: str, message: Dict, channel: str = 'web') -> bool:
        """
        Add message to conversation history
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            message: Dict containing message data
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: Success status
        """
        session_data = self.get_session(user_identifier, channel)
        
        if not session_data:
            return False
        
        conversation_history = session_data.get('conversation_history', [])
        message['timestamp'] = datetime.now().isoformat()
        conversation_history.append(message)
        
        # Keep only last 50 messages to prevent memory issues
        if len(conversation_history) > 50:
            conversation_history = conversation_history[-50:]
        
        return self.update_session(user_identifier, {
            'conversation_history': conversation_history
        }, channel)
    
    def escalate_session(self, user_identifier: str, reason: str, channel: str = 'web') -> bool:
        """
        Mark session as escalated
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            reason: Reason for escalation
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: Success status
        """
        return self.update_session(user_identifier, {
            'escalated': True,
            'escalation_reason': reason,
            'escalation_time': datetime.now().isoformat()
        }, channel)
    
    def delete_session(self, user_identifier: str, channel: str = 'web') -> bool:
        """
        Delete a session
        
        Args:
            user_identifier: Phone number for WhatsApp, session_id for web
            channel: 'web' or 'whatsapp'
            
        Returns:
            bool: Success status
        """
        session_key = f"session:{channel}:{user_identifier}"
        try:
            result = self.redis_client.delete(session_key)
            logger.info(f"Deleted session for {user_identifier} on {channel}")
            return result > 0
        except Exception as e:
            logger.error(f"❌ Failed to delete session: {e}")
            return False
    
    def get_active_sessions_count(self, channel: str = None) -> int:
        """
        Get count of active sessions
        
        Args:
            channel: Optional channel filter ('web' or 'whatsapp')
            
        Returns:
            int: Number of active sessions
        """
        try:
            if channel:
                pattern = f"session:{channel}:*"
            else:
                pattern = "session:*"
            
            return len(self.redis_client.keys(pattern))
        except Exception as e:
            logger.error(f"❌ Failed to get session count: {e}")
            return 0

# Initialize global session manager
session_manager = SessionManager()