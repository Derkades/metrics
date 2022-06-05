import sqlite3
from pathlib import Path
from os import environ as env

DATA_PATH = Path(env['DATA_PATH'] if 'DATA_PATH' in env else '.')

if __name__ == '__main__':
    db_path = Path(DATA_PATH, 'metrics.db')
    print('Creating tables in database', db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute('''
                CREATE TABLE IF NOT EXISTS clients (
                    source TEXT NOT NULL,
                    uuid TEXT NOT NULL,
                    last_update TIMESTAMP NOT NULL,
                    UNIQUE(source, uuid)
                )
                ''')
    cur.execute('''
                CREATE TABLE IF NOT EXISTS metrics (
                    client_id INTEGER NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value TEXT NOT NULL,
                    FOREIGN KEY (client_id) REFERENCES clients (rowid) ON DELETE CASCADE,
                    UNIQUE(client_id, metric_name)
                )
                ''')
    con.commit()
    con.close()
