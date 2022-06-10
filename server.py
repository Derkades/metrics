import json
from pathlib import Path
import yaml
from flask import Flask
from flask import request, Response
import sqlite3
from datetime import datetime, timedelta
from uuid import UUID
import html
from os import environ as env
from werkzeug.exceptions import HTTPException
import sys
import traceback
from threading import Thread
import time


DATA_PATH = Path(env['DATA_PATH'] if 'DATA_PATH' in env else '.')
CONFIG_PATH = Path(env['CONFIG_PATH'] if 'CONFIG_PATH' in env else '.')


def open_db():
    db_path = Path(DATA_PATH, 'metrics.db')
    db = sqlite3.connect(db_path,
                         detect_types=sqlite3.PARSE_DECLTYPES)
    db.execute('PRAGMA foreign_keys=ON')
    return db


db = open_db()

application = Flask(__name__)

configs = {}

for path in Path(CONFIG_PATH, 'sources').iterdir():
    if not path.name.endswith('.yaml'):
        print('Skipped config file ', path.name, flush=True)
    with path.open() as f:
        configs[path.name[:-5]] = yaml.safe_load(f)
    print('Loaded source:', path.name, flush=True)


@application.route('/show', methods=['GET'])
def show():
    if 'source' not in request.args:
        return Response('Specify source', 400)

    source = request.args['source']

    if source not in configs:
        return Response('Invalid source', 400)

    response = "<h2>Viewing metrics for " + html.escape(source) + "</h2>"

    cur = db.cursor()
    cur.execute('SELECT COUNT(*) FROM clients WHERE source=?', (source,))
    (count_clients,) = cur.fetchone()

    response += f'Total active: {count_clients}'

    cur.execute('''
                SELECT metric_name, metric_value, COUNT(*) as value_count
                FROM metrics
                    JOIN clients ON metrics.client_id = clients.rowid
                WHERE clients.source=?
                GROUP BY metric_name, metric_value
                ORDER BY metric_name ASC, value_count DESC
                ''', (source,))

    by_metric = {}

    for metric_name, metric_value, count in cur.fetchall():
        if metric_name in by_metric:
            by_metric[metric_name].append((metric_value, count))
        else:
            by_metric[metric_name] = [(metric_value, count)]

    cur.close()

    for metric_name, values in by_metric.items():
        response += "<h3>" + html.escape(metric_name) + "</h3>"
        response += "<ol>"
        for metric_value, count in values:
            response += f"<li>{html.escape(metric_value)} ({count})</li>"
        response += "</ol>"

    return response


@application.route('/submit', methods=['POST'])
# @compress.compressed()
def submit():
    if 'source' not in request.json:
        return Response('Missing source', 400)

    source = request.json['source']

    if source not in configs:
        return Response('Invalid source', 400)

    if 'uuid' not in request.json:
        return Response('Missing uuid', 400)
    uuid = request.json['uuid']
    try:
        # Confirm UUID is valid, and add dashes if they are missing
        uuid = str(UUID(hex=uuid))
    except ValueError:
        return Response('Invalid UUID', 400)

    if 'fields' not in request.json:
        return Response('Missing fields', 400)

    conf_inp = configs[source]['input']

    field_values = {}

    for field in conf_inp['fields']:
        name = field['name']
        if name not in request.json['fields']:
            # If optional and not present, skip this field
            if 'optional' in field and field['optional'] is True:
                continue
            # If null value is specified, use that and skip this field
            elif 'null_value' in field:
                field_values[name] = field['null_value']
                continue
            else:
                return Response(f"Missing field '{name}'", 400)

        value = request.json['fields'][name]

        # Same check as above, but this time for null value instead of a non-existing key
        if value is None:
            if 'optional' in field and field['optional'] is True:
                continue
            elif 'null_value' in field:
                field_values[name] = field['null_value']
                continue
            else:
                return Response(f"Field '{name}' is not nullable but null was given")

        correct_type = field['type']
        if correct_type == 'string':
            if not isinstance(value, str):
                return Response(f"Field '{name}' must be a string", 400)

            if 'allow_only' in field and value not in field['allow_only']:
                return Response(f"Field value '{value}' not allowed for field '{name}'.", 400)

            field_values[name] = value
        elif correct_type == 'boolean':
            if not isinstance(value, bool):
                return Response(f"Field '{name}' must be a boolean", 400)

            field_values[name] = str(value)
        elif correct_type == 'integer':
            if not isinstance(value, int):
                return Response(f"Field '{name}' must be an integer", 400)

            field_values[name] = str(value)
        else:
            raise Exception(f"Unknown type '{correct_type}'")

    cur = db.cursor()
    cur.execute('''
                SELECT id, last_update
                FROM clients
                WHERE uuid=? AND source=?
                ''', (uuid, source))
    row = cur.fetchone()
    now = datetime.now()
    if row is None:
        cur.execute('INSERT INTO clients (source, uuid, last_update) VALUES (?, ?, ?) RETURNING `id`', (source, uuid, now))
        (client_id,) = cur.fetchone()
        print(f"Received data for source {source} from id {client_id} address {request.remote_addr} for the first time", flush=True)
    else:
        frequency = conf_inp['frequency_minutes']
        client_id, last_update = row
        minutes_ago = int((now - last_update).total_seconds()) / 60
        if minutes_ago < frequency * 0.9:
            print(f"Received data for source {source} from id {client_id} address {request.remote_addr}, previously {minutes_ago:.1f} minutes ago (ignored, too quickly)", flush=True)
            return Response(f'Please wait {frequency} minutes in between requests', 429)
        else:
            cur.execute('''
                        UPDATE clients
                        SET last_update=?
                        WHERE id=?
                        ''', (now, client_id))

        print(f"Received data for source {source} from id {client_id} address {request.remote_addr}, previously {minutes_ago:.1f} minutes ago", flush=True)

    print(field_values, flush=True)

    insert_data = []

    for name, value in field_values.items():
        insert_data.append((client_id, name, value, value))

    cur.executemany('''
                    INSERT INTO metrics (client_id, metric_name, metric_value)
                    VALUES (?, ?, ?)
                    ON CONFLICT (client_id, metric_name)
                        DO UPDATE SET metric_value=?
                    ''', insert_data)

    cur.close()
    db.commit()

    return Response('ok', 200)


@application.errorhandler(HTTPException)
def handle_exception(e: HTTPException):
    print(e, file=sys.stderr)
    traceback.print_exc()
    return e.get_response()


class PurgeExpired(Thread):

    def __init__(self):
        Thread.__init__(self, daemon=True)

    def run(self):
        while True:
            time.sleep(10)
            with open_db() as db:
                cur = db.cursor()

                for source, config in configs.items():
                    print('Pruning expired data for', source, flush=True)
                    expiry_minutes = config['input']['expire_minutes']
                    delete_before = datetime.now() - timedelta(minutes=expiry_minutes)
                    cur.execute('DELETE FROM clients WHERE source = ? and last_update < ?', (source, delete_before))
                    time.sleep(1)

                cur.close()
                db.commit()

            time.sleep(300)


PurgeExpired().start()
