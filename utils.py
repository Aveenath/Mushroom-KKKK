import sqlite3
import datetime

def get_db_connection():
    return sqlite3.connect('mushroom_client.db')

def get_local_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)
