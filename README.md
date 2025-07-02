# Financial Chatbot

## Setup

### Prerequisites
- Python 3.8 or higher
- pip (Python package installer)

### Installation

1. Clone the repository:
    ```bash
    git clone https://github.com/yourusername/financial_chatbot.git
    cd financial_chatbot
    ```

2. Create a virtual environment (recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use: venv\Scripts\activate
    ```

3. Install required packages:
    ```bash
    pip install -r requirements.txt
    ```

### Required Modules
- `pandas`: For data manipulation
- `numpy`: For numerical operations
- `transformers`: For NLP models
- `torch`: For deep learning
- `flask`: For web application (if applicable)
- `matplotlib`: For data visualization
- `scikit-learn`: For machine learning algorithms

## Running the Application

1. Start the chatbot:
    ```bash
    python src/main.py
    ```

2. For the web interface (if applicable):
    ```bash
    python src/app.py
    ```

3. For training the model:
    ```bash
    python src/train.py
    ```

## Configuration

Modify `config.json` to adjust the chatbot parameters and settings.

## Project Structure
```
financial_chatbot/
├── src/
│   ├── main.py         # Main entry point
│   ├── app.py          # Web interface
│   ├── train.py        # Model training
│   └── utils/          # Utility functions
├── data/               # Dataset files
├── models/             # Saved models
├── config.json         # Configuration file
└── README.md           # This file
```