#!/bin/bash

# OMI Agent Client Deep Reset Script
# Cleans all non-essential, generated, and git-ignored files.

echo "🚀 Starting deep cleanup of OMI Agent Client..."

# Check if running as root for certain operations
if [ "$EUID" -ne 0 ]; then
    echo "⚠️  Note: Not running as root. Some system-owned files (like root-owned pycache) might not be cleared."
    echo "   If you see 'Permission denied' errors, try: sudo ./reset.sh"
fi

# 0. Stop Running Services
echo "🛑 Stopping running services..."
sudo pkill -f "service.py" 2>/dev/null
sudo pkill -f "midiwebui.py" 2>/dev/null
sudo pkill -f "uvicorn" 2>/dev/null
sudo pkill -f "client.py" 2>/dev/null
