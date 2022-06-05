#!/bin/bash
set -e
python3 create_database.py
exec gunicorn -b 0.0.0.0:8080 server
