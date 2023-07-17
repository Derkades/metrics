from pathlib import Path
import yaml
import sqlite3
from datetime import datetime, timedelta
from uuid import UUID
from os import environ as env
import sys
import traceback
from threading import Thread
import time
import re
import math

from flask import Flask, request, render_template, Response
from werkzeug.exceptions import HTTPException


DATA_PATH = Path(env['DATA_PATH'] if 'DATA_PATH' in env else '.')
CONFIG_PATH = Path(env['CONFIG_PATH'] if 'CONFIG_PATH' in env else '.')

BAR_COLOR_MAIN = ['4f90c9', 'fad98c', 'ed6484', '7ef2d4', 'ba7ef2', 'e09472', 'e079ad']
BAR_COLOR_OTHER = '777'


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

    conf_show = configs[source]['show']

    cur = db.cursor()

    cur.execute('SELECT COUNT(*) FROM clients WHERE source=?', (source,))
    (count_clients,) = cur.fetchone()

    context_items = []

    for item in conf_show['items']:
        field: str = item['field']
        title: str = item['title']
        item_type: str = item['type'] if 'type' in item else 'breakdown'

        context_item = {}
        context_item['title'] = title
        context_item['type'] = item_type
        context_items.append(context_item)

        if item_type == 'breakdown':
            context_item_values = []
            context_item_bars = []
            context_item['values'] = context_item_values
            context_item['bars'] = context_item_bars
            context_item['bar_other_color'] = BAR_COLOR_OTHER

            cur.execute('''
                        SELECT metric_value, COUNT(*) AS value_count
                        FROM metrics
                            JOIN clients ON metrics.client_id = clients.id
                        WHERE clients.source = ? AND metric_name = ?
                        GROUP BY metric_value
                        ORDER BY value_count DESC
                        ''', (source, field))

            values: dict[str, str] = {}
            for value, count in cur.fetchall():
                if 'transform' in item:
                    for transform in item['transform']:
                        if transform['type'] == 'map':
                            for map_from, map_to in transform['map'].items():
                                if value == map_from:
                                    value = map_to
                        elif transform['type'] == 'regex':
                            groups = re.findall(transform['pattern'], value)
                            if len(groups) >= 1:
                                matches = groups[0]
                                for match in matches:
                                    if isinstance(match, str) and match != '':
                                        value = match
                                        break
                                else:
                                    value = None
                            else:
                                value = None
                        else:
                            raise ValueError('Invalid transform type:' + str(transform['type']))

                if value is not None:
                    if value in values:
                        values[value] += count
                    else:
                        values[value] = count

            if len(values) >= 1:
                total_count = sum(value for value in values.values())
                for i, (metric_value, count) in enumerate(values.items()):
                    if 'limit' in item and i >= int(item['limit']):
                        skipped = len(values) - int(item['limit'])
                        context_item['skipped'] = skipped
                        break

                    perc = (count / total_count) * 100
                    context_item_value = {'value': metric_value, 'count': count, 'perc': f'{perc:.1f}'}
                    context_item_values.append(context_item_value)
                    if i < len(BAR_COLOR_MAIN) and perc > 1.5:
                        context_item_value['color'] = BAR_COLOR_MAIN[i]
                        context_item_bars.append({'width': math.floor(perc * 100) / 100, 'color': BAR_COLOR_MAIN[i], 'index': i + 1})
            else:
                pass

        elif item_type == 'summary':
            cur.execute('''
                        SELECT SUM(metric_value) AS value_sum, AVG(metric_value) value_mean
                        FROM metrics
                            JOIN clients ON metrics.client_id = clients.id
                        WHERE clients.source = ? AND metric_name = ?
                        ''',
                        (source, field))
            (value_sum, value_mean) = cur.fetchone()
            context_item['sum'] = value_sum
            context_item['mean'] = value_mean
        else:
            raise ValueError('Invalid type: ' + str(item_type))

    cur.close()

    return render_template('view-metrics.jinja',
                           title=conf_show['title'],
                           count_clients=count_clients,
                           items=context_items)


def bad_response(message):
    print(message)
    return Response(message, 400)


@application.route('/submit', methods=['POST'])
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
                print('Missing field:', name)
                return bad_response(f"Missing field '{name}'")

        value = request.json['fields'][name]

        # Same check as above, but this time for null value instead of a non-existing key
        if value is None:
            if 'optional' in field and field['optional'] is True:
                continue
            elif 'null_value' in field:
                field_values[name] = field['null_value']
                continue
            else:
                return bad_response(f"Field '{name}' is not nullable but null was given")

        correct_type = field['type']
        if correct_type == 'string':
            if not isinstance(value, str):
                return bad_response(f"Field '{name}' must be a string")

            if 'allow_only' in field and value not in field['allow_only']:
                print('Received not allowed value :', name)
                return bad_response(f"Field value '{value}' not allowed for field '{name}'.")

            field_values[name] = value
        elif correct_type == 'boolean':
            if not isinstance(value, bool):
                return bad_response(f"Field '{name}' must be a boolean")

            field_values[name] = str(value)
        elif correct_type == 'integer':
            if not isinstance(value, int):
                return bad_response(f"Field '{name}' must be an integer")

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
