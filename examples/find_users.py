import sqlite3


def find_users(db: sqlite3.Connection, names=[]):
    results = []
    for name in names:
        query = f"SELECT id, name FROM users WHERE name = '{name}'"
        results.append(db.execute(query).fetchone())
    return results
