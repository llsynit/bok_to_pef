#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Check if virtual environment directory exists
if [ ! -d "venv" ]; then
    echo "Virtual environment 'venv' not found. Creating one..."
    python3 -m venv venv
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install requirements
echo "Installing/updating dependencies from requirements.txt..."
pip install -r requirements.txt

# Start the uvicorn server
echo "Starting uvicorn server..."
exec uvicorn app:app --host 0.0.0.0 --port 39013 --reload