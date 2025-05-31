# gemini_client.py

import google.generativeai as genai
import os
genai.configure(api_key=os.environ["GEMINI_API_KEY"])


# Use Gemini 1.5 Flash model
model = genai.GenerativeModel(model_name="models/gemini-1.5-flash-latest")

def generate_response(query_type, data):
    # Create the input prompt
    prompt = f"User asked about: {query_type}\n\nDetails:\n{data}"
    
    # Generate response using Gemini
    response = model.generate_content(prompt)
    
    return response.text  # Extract the generated text
