#!/bin/bash
cd "$(dirname "$0")"

echo "========================================"
echo "Saving changes to GitHub..."
echo "========================================"

# Add all changes
echo "Adding all changes..."
git add .
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to add changes"
    exit 1
fi

# Check if there are changes to commit
if git diff --cached --quiet; then
    echo "[INFO] No changes to commit"
    exit 0
fi

# Create commit with timestamp
echo "Creating commit..."
timestamp=$(date "+%Y-%m-%d %H:%M:%S")
git commit -m "Auto-save $timestamp"
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to create commit"
    exit 1
fi

# Push to GitHub
echo "Pushing to GitHub..."
git push origin master
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to push to GitHub"
    exit 1
fi

echo "========================================"
echo "Successfully saved to GitHub!"
echo "========================================"
