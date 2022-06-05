FROM python:3-slim

COPY requirements.txt /

RUN pip install -r /requirements.txt

COPY docker/entrypoint.sh /

COPY create_database.py server.py /app/

WORKDIR /app

ENV DATA_PATH="/data"
ENV CONFIG_PATH="/config"

ENTRYPOINT ["bash", "/entrypoint.sh"]
