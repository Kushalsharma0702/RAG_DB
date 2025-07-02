import re
import logging

def classify_intent(message: str) -> str:
    """
    A simple rule-based classifier that extracts intent from a message.
    It will first check for explicit agent requests, then for financial keywords.
    
    Args:
        message (str): The user's message
        
    Returns:
        str: 'emi', 'balance', 'loan', 'agent_escalation', or 'unclear'
    """
    if not message:
        return "unclear"
    
    # Convert to lowercase for case-insensitive matching
    message = message.lower()
    
    # --- Check for explicit agent request patterns first ---
    agent_request_patterns = [
        r'speak.*agent', r'talk.*agent', r'human', r'real person',
        r'customer service', r'representative', r'speak.*person', r'talk.*person',
        r'connect.*agent', r'transfer.*agent', r'live chat',
        r'i need help', r'not.*understand', r'confused', r'complicated', r'complex', r'difficult' # Phrases indicating need for human
    ]
    
    for pattern in agent_request_patterns:
        if re.search(pattern, message):
            logging.info(f"Intent classified as 'agent_escalation' by rules for message: '{message}'")
            return "agent_escalation"
    
    # --- Financial keywords ---
    # EMI related keywords
    emi_patterns = [
        r'\bemi\b', 
        r'installment', 
        r'monthly payment',
        r'next payment',
        r'payment date',
        r'when.*due',
        r'payment.*history',
        r'recent payment',
        r'amount due'
    ]
    
    # Balance related keywords
    balance_patterns = [
        r'\bbalance\b',
        r'how much.*account',
        r'how much.*money',
        r'available funds',
        r'credit.*available',
        r'account.*status',
        r'account.*amount',
        r'funds'
    ]
    
    # Loan related keywords
    loan_patterns = [
        r'\bloan\b',
        r'principal',
        r'interest rate',
        r'borrow',
        r'lending',
        r'credit', # Can overlap with balance, but more specific to loan context
        r'tenure',
        r'loan.*amount',
        r'loan.*status',
        r'loan.*type',
        r'apply for loan',
        r'new loan'
    ]
    
    # Check for EMI patterns
    for pattern in emi_patterns:
        if re.search(pattern, message):
            logging.info(f"Intent classified as 'emi' by rules for message: '{message}'")
            return "emi"
    
    # Check for Balance patterns
    for pattern in balance_patterns:
        if re.search(pattern, message):
            logging.info(f"Intent classified as 'balance' by rules for message: '{message}'")
            return "balance"
    
    # Check for Loan patterns
    for pattern in loan_patterns:
        if re.search(pattern, message):
            logging.info(f"Intent classified as 'loan' by rules for message: '{message}'")
            return "loan"
    
    # Check for explicit button text matches (for web UI options)
    if "my emi" in message:
        logging.info(f"Intent classified as 'emi' by button text match: '{message}'")
        return "emi"
    elif "my account balance" in message:
        logging.info(f"Intent classified as 'balance' by button text match: '{message}'")
        return "balance"
    elif "my loan amount" in message or "my loan" in message:
        logging.info(f"Intent classified as 'loan' by button text match: '{message}'")
        return "loan"
    elif "speak to agent" in message: # Explicit "Speak to Agent" button
        logging.info(f"Intent classified as 'agent_escalation' by button text match: '{message}'")
        return "agent_escalation"

    # If no patterns match, intent is unclear
    logging.info(f"Intent classified as 'unclear' by rules for message: '{message}'")
    return "unclear"

