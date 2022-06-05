# Metrics server

Simple server to collect metrics sent by software.

## Installation
Create database
```
python3 create_database.py
```

Start server
```
gunicorn server
```
or on all interfaces:
```
gunicorn -b 0.0.0.0 server
```
