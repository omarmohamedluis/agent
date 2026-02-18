#!/bin/bash

# OMI Agent Update Script
# Executed independently from the main agent process.

UPDATE_CONFIG="/tmp/omi_update.json"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "--- OMI Agent Update Started ---"
echo "Repository directory: $REPO_DIR"

# 1. Wait for agent to stop
echo "Waiting for agent to stop..."
sleep 5

if [ ! -f "$UPDATE_CONFIG" ]; then
    echo "Error: No update config found at $UPDATE_CONFIG"
    exit 1
fi

# Extract branch using python (more reliable than grep/sed for JSON)
BRANCH=$(python3 -c "import json; print(json.load(open('$UPDATE_CONFIG'))['branch'])")

if [ -z "$BRANCH" ]; then
    echo "Error: No branch specified in $UPDATE_CONFIG"
    exit 1
fi

echo "Updating to branch: $BRANCH"

cd "$REPO_DIR" || exit 1

# 2. Git Operations
echo "Fetching updates..."
git fetch --all

echo "Checking out branch $BRANCH..."
git checkout "$BRANCH" || { echo "Failed to checkout branch $BRANCH"; exit 1; }

echo "Pulling latest changes..."
git pull origin "$BRANCH" || { echo "Failed to pull changes"; exit 1; }

# 3. Cleanup and Reboot
echo "Cleaning up..."
rm "$UPDATE_CONFIG"

echo "Update complete. Rebooting in 5 seconds..."
sleep 5
sudo reboot
