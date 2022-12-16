FROM python:3-slim

COPY requirements.txt /

RUN pip install --no-cache-dir -r /requirements.txt

# Newer sqlite version (somewhat hacky but it works)
RUN echo "deb http://ftp.de.debian.org/debian bookworm main" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install --only-upgrade -y libsqlite3-0 && \
    rm -rf /var/lib/apt/lists/*

COPY docker/entrypoint.sh /

COPY create_database.py server.py /app/
COPY templates/ app/templates/

WORKDIR /app

ENV DATA_PATH="/data"
ENV CONFIG_PATH="/config"

ENTRYPOINT ["bash", "/entrypoint.sh"]
