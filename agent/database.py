"""
agent/database.py — Database connection layer
POC  : SQLite (zero setup)
Prod : Swap get_connection() for psycopg2 Redshift
"""

import sqlite3
import os

DB_PATH = "poc_database.db"


def get_connection(username: str = "default_user"):
    """
    POC SQLite connection.
    Production Redshift swap:
        import psycopg2
        return psycopg2.connect(
            host=os.getenv("REDSHIFT_HOST"),
            port=int(os.getenv("REDSHIFT_PORT", 5439)),
            dbname=os.getenv("REDSHIFT_DBNAME"),
            user=username,
            password=os.getenv("REDSHIFT_PASSWORD")
        )
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def setup_sample_database():
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id      INTEGER PRIMARY KEY,
            customer_id   INTEGER NOT NULL,
            product_name  TEXT    NOT NULL,
            quantity      INTEGER NOT NULL,
            unit_price    REAL    NOT NULL,
            total_amount  REAL    NOT NULL,
            order_date    TEXT    NOT NULL,
            region        TEXT    NOT NULL,
            status        TEXT    NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            customer_id   INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            email         TEXT NOT NULL,
            country       TEXT NOT NULL,
            segment       TEXT NOT NULL,
            created_date  TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            product_id   INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            category     TEXT NOT NULL,
            unit_price   REAL NOT NULL,
            stock_qty    INTEGER NOT NULL,
            supplier     TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_returns (
            return_id     INTEGER PRIMARY KEY,
            order_id      INTEGER NOT NULL,
            return_date   TEXT    NOT NULL,
            reason        TEXT    NOT NULL,
            refund_amount REAL    NOT NULL
        )
    """)

    cursor.executemany("INSERT OR IGNORE INTO customers VALUES (?,?,?,?,?,?)", [
        (1, "Alice Johnson",  "alice@example.com",  "USA",     "Enterprise", "2022-01-15"),
        (2, "Bob Smith",      "bob@example.com",    "UK",      "SMB",        "2022-03-22"),
        (3, "Carol Williams", "carol@example.com",  "Canada",  "Enterprise", "2022-05-10"),
        (4, "David Brown",    "david@example.com",  "USA",     "SMB",        "2023-01-08"),
        (5, "Eva Martinez",   "eva@example.com",    "Germany", "Enterprise", "2023-04-17"),
        (6, "Frank Lee",      "frank@example.com",  "USA",     "SMB",        "2023-06-01"),
        (7, "Grace Kim",      "grace@example.com",  "Japan",   "Enterprise", "2023-08-15"),
        (8, "Henry Wilson",   "henry@example.com",  "UK",      "SMB",        "2024-01-10"),
    ])
    cursor.executemany("INSERT OR IGNORE INTO products VALUES (?,?,?,?,?,?)", [
        (1, "Laptop Pro",    "Electronics", 1200.00, 45,  "TechSupply Co"),
        (2, "Office Chair",  "Furniture",    350.00, 120, "FurniturePlus"),
        (3, "Keyboard",      "Electronics",   85.00, 200, "TechSupply Co"),
        (4, "Monitor 27in",  "Electronics",  420.00,  60, "ScreenWorld"),
        (5, "Standing Desk", "Furniture",    650.00,  30, "FurniturePlus"),
        (6, "Webcam HD",     "Electronics",   95.00,  80, "TechSupply Co"),
        (7, "Desk Lamp",     "Furniture",     45.00, 150, "FurniturePlus"),
        (8, "USB Hub",       "Electronics",   35.00, 300, "TechSupply Co"),
    ])
    cursor.executemany("INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?,?,?,?)", [
        (1001,1,"Laptop Pro",   2,1200.00,2400.00,"2024-01-10","West", "Completed"),
        (1002,2,"Office Chair", 5, 350.00,1750.00,"2024-01-15","East", "Completed"),
        (1003,3,"Keyboard",    10,  85.00, 850.00,"2024-02-01","North","Completed"),
        (1004,1,"Monitor 27in", 3, 420.00,1260.00,"2024-02-14","West", "Pending"),
        (1005,4,"Standing Desk",1, 650.00, 650.00,"2024-03-05","South","Completed"),
        (1006,5,"Laptop Pro",   4,1200.00,4800.00,"2024-03-20","East", "Completed"),
        (1007,2,"Keyboard",     7,  85.00, 595.00,"2024-04-01","East", "Pending"),
        (1008,3,"Monitor 27in", 2, 420.00, 840.00,"2024-04-18","North","Completed"),
        (1009,4,"Office Chair", 3, 350.00,1050.00,"2024-05-02","South","Cancelled"),
        (1010,5,"Standing Desk",2, 650.00,1300.00,"2024-05-15","East", "Completed"),
        (1011,6,"Webcam HD",    5,  95.00, 475.00,"2024-06-01","West", "Completed"),
        (1012,7,"USB Hub",     10,  35.00, 350.00,"2024-06-10","North","Completed"),
        (1013,8,"Desk Lamp",    4,  45.00, 180.00,"2024-07-01","East", "Completed"),
        (1014,1,"Keyboard",     6,  85.00, 510.00,"2024-07-15","West", "Completed"),
        (1015,3,"Laptop Pro",   1,1200.00,1200.00,"2024-08-01","North","Pending"),
    ])
    cursor.executemany("INSERT OR IGNORE INTO order_returns VALUES (?,?,?,?,?)", [
        (1,1009,"2024-05-10","Damaged on arrival",1050.00),
        (2,1002,"2024-02-01","Wrong item shipped",  350.00),
        (3,1007,"2024-04-20","Changed mind",        595.00),
    ])

    conn.commit()
    conn.close()
    print(f"✅ Sample database created at: {DB_PATH}")


if __name__ == "__main__":
    setup_sample_database()
