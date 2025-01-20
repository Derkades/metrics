FROM python:3.13-slim

COPY requirements.txt /

RUN pip install --no-cache-dir -r /requirements.txt

COPY docker/entrypoint.sh /

COPY create_database.py server.py /app/
COPY templates/ app/templates/

WORKDIR /app

ENV DATA_PATH="/data"
ENV CONFIG_PATH="/config"

ENTRYPOINT ["bash", "/entrypoint.sh"]
