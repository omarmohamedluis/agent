#!/bin/bash

# OMI Agent Client Reset Script
# Cleans logs, temporary state files, and pycache to reset the application.

echo "Cleaning up OMI Agent Client..."

# 1. Clear Logs
if [ -d "logs" ]; then
    echo "Clearing logs..."
    rm -rf logs/*
    # Recreate empty log directories if needed, or let the app handle it
    mkdir -p logs/services
fi

# 2. Reset Active Service State
if [ -f "data/active_service.txt" ]; then
    echo "Resetting active service state..."
    echo "STANDBY" > data/active_service.txt
fi

# 3. Clear Pycache
echo "Clearing __pycache__..."
find . -type d -name "__pycache__" -exec rm -rf {} +

# 4. Clear any temporary files in data (if any other than structure.json)
# Note: We preserve structure.json as it likely contains config.

echo "Done! Application state reset."
