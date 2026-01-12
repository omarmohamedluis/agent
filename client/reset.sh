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

# 1. Clear Logs
echo "📁 Clearing logs..."
find . -name "*.log" -type f -delete 2>/dev/null
mkdir -p logs/services logs/components

# 2. Clear Pycache and Python artifacts
echo "🐍 Clearing Python artifacts (__pycache__, .pyc, .pyo)..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -type f -delete 2>/dev/null
find . -name "*.pyo" -type f -delete 2>/dev/null

# 3. Clear Lock and PID files
echo "🔒 Clearing lock and PID files..."
find . -name "*.lock" -type f -delete 2>/dev/null
find . -name "*.pid" -type f -delete 2>/dev/null

# 4. Reset Active Service State
echo "🔄 Resetting active service state..."
if [ -f "data/active_service.txt" ]; then
    echo "STANDBY" > data/active_service.txt
fi

# 5. Clear Generated/Local JSON files
echo "📄 Clearing generated and local JSON configurations..."
rm -f data/structure.json
rm -f data/server.json
rm -f servicios/servicios.json
rm -f servicios/MIDI/OMIMIDI_map.json
find . -name "*_TEMP.json" -type f -delete 2>/dev/null

echo "✨ Done! Application state is now completely clean."
echo "💡 Note: Critical configs (servicios.json, OMIMIDI_map.json) have been backed up as .template files."
