import re

def classify_intent(message):
    """
    A simple rule-based classifier that extracts intent from a message.
    
    Args:
        message (str): The user's message
        
    Returns:
        str: 'emi', 'balance', 'loan', or 'unclear'
    """
    if not message:
        return "unclear"
    
    # Convert to lowercase for case-insensitive matching
    message = message.lower()
    
    # EMI related keywords
    emi_patterns = [
        r'\bemi\b', 
        r'installment', 
        r'monthly payment',
        r'next payment',
        r'payment date',
        r'when.*due',
        r'payment.*history',
        r'recent payment'
    ]
    
    # Balance related keywords
    balance_patterns = [
        r'\bbalance\b',
        r'how much.*account',
        r'how much.*money',
        r'available funds',
        r'credit.*available',
        r'account.*status',
        r'account.*amount'
    ]
    
    # Loan related keywords
    loan_patterns = [
        r'\bloan\b',
        r'principal',
        r'interest rate',
        r'borrow',
        r'lending',
        r'credit',
        r'tenure',
        r'loan.*amount',
        r'loan.*status',
        r'loan.*type'
    ]
    
    # Check for EMI patterns
    for pattern in emi_patterns:
        if re.search(pattern, message):
            return "emi"
    
    # Check for Balance patterns
    for pattern in balance_patterns:
        if re.search(pattern, message):
            return "balance"
    
    # Check for Loan patterns
    for pattern in loan_patterns:
        if re.search(pattern, message):
            return "loan"
    
    # Button text matches
    if "my emi" in message:
        return "emi"
    elif "my account balance" in message:
        return "balance"
    elif "my loan amount" in message or "my loan" in message:
        return "loan"
    
    # If no patterns match, intent is unclear
    return "unclear"
