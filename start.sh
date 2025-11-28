#!/bin/sh
set -e
sudo sh -c 'nohup .venv/bin/python -u toolbox-service-app.py >> app.log 2>&1 & echo $! > app.pid'
echo "Started. PID=$(cat app.pid)"

