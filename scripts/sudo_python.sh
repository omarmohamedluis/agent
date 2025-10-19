#!/bin/bash
cd /home/omar/omi/agent
exec sudo -E /home/omar/omi/agent/.venv/bin/python "$@"
EOF
chmod +x scripts/sudo_python.sh
