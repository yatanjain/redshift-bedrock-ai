"""
agent/database.py — Database connection layer

╔══════════════════════════════════════════════════════════════╗
║  CURRENT MODE : SQLite (POC)                                 ║
║  PROD MODE    : Amazon Redshift (commented out below)        ║
║                                                              ║
║  To switch to Redshift:                                      ║
║    1. pip install psycopg2-binary                            ║
║    2. Fill in REDSHIFT_* values in .env                      ║
║    3. Uncomment Redshift section in get_connection()         ║
║    4. Comment out SQLite section in get_connection()         ║
║    5. Remove USER_PERMISSIONS, get_allowed_tables()          ║
║    6. Remove _check_permission() from tools.py               ║
║    7. Update SQL queries in tools.py (see comments there)    ║
╚══════════════════════════════════════════════════════════════╝

Key Permission Difference — SQLite POC vs Redshift Production:

  SQLite (current):
    ❌ No native permission system
    ✅ Simulated via USER_PERMISSIONS dict below
    ✅ Every tool calls _check_permission() before executing
    ✅ RAG builds per-user index filtering by allowed tables

  Redshift (production):
    ✅ Native permission system — GRANT/REVOKE SQL commands
    ✅ Connect as actual user → DB enforces automatically
    ✅ information_schema.tables returns ONLY accessible tables
    ✅ RAG auto-generates correct schema per user (no filtering needed)
    ❌ USER_PERMISSIONS dict not needed — delete it
    ❌ _check_permission() not needed — delete it from tools.py
    ❌ Permission filtering in tools.py not needed — delete all

  In short: Redshift does in 0 lines what SQLite needs ~100 lines for.
"""

import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "poc_database.db"


# ══════════════════════════════════════════════════════════════
# POC — SQLite Permission Simulation
# Simulates Redshift GRANT/REVOKE permission system
# In Redshift: DBA runs → GRANT SELECT ON table TO username
# In SQLite:   We maintain this dict manually
# ══════════════════════════════════════════════════════════════

USER_PERMISSIONS = {

    # Default user — access to business tables only
    # Equivalent Redshift commands:
    #   GRANT SELECT ON orders          TO default_user;
    #   GRANT SELECT ON customers       TO default_user;
    #   GRANT SELECT ON products        TO default_user;
    #   GRANT SELECT ON order_returns   TO default_user;
    #   (no GRANT for employee_salaries → restricted)
    "default_user": [
        "orders",
        "customers",
        "products",
        "order_returns",
    ],

    # Admin user — access to all tables including restricted
    # Equivalent Redshift command:
    #   GRANT SELECT ON ALL TABLES IN SCHEMA public TO admin;
    "admin": [
        "orders",
        "customers",
        "products",
        "order_returns",
        "employee_salaries",
    ],

    # Analyst — sales data only, no HR or customer PII
    # Equivalent Redshift commands:
    #   GRANT SELECT ON orders   TO analyst;
    #   GRANT SELECT ON products TO analyst;
    "analyst": [
        "orders",
        "products",
    ],
}

# Fallback if username not found in USER_PERMISSIONS
DEFAULT_PERMISSIONS = [
    "orders",
    "customers",
    "products",
    "order_returns",
]


def get_allowed_tables(username: str) -> list:
    """
    Returns list of tables this user is allowed to access.

    POC  : Reads from USER_PERMISSIONS dict above
    Prod : DELETE THIS FUNCTION — Redshift enforces automatically
           when you connect as the actual user via get_connection()
    """
    return USER_PERMISSIONS.get(username, DEFAULT_PERMISSIONS)


def get_connection(username: str = "default_user"):
    """
    Returns a database connection for the given user.

    ── CURRENT MODE: SQLite (POC) ────────────────────────────
    Simple local file database — no cloud setup needed.
    Permissions are simulated via USER_PERMISSIONS dict above.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


    # ══════════════════════════════════════════════════════════
    # PRODUCTION REDSHIFT — Password Authentication
    # ══════════════════════════════════════════════════════════
    # To activate:
    #   1. Comment out SQLite block above
    #   2. Uncomment this block
    #   3. pip install psycopg2-binary
    #   4. Fill .env: REDSHIFT_HOST, REDSHIFT_PORT,
    #                 REDSHIFT_DBNAME, REDSHIFT_PASSWORD
    #
    # How permissions work in Redshift:
    #   - Connect as the actual user (user=username)
    #   - Redshift automatically enforces all GRANT/REVOKE rules
    #   - information_schema.tables only shows accessible tables
    #   - Queries on restricted tables raise PermissionError
    #   - No USER_PERMISSIONS dict needed — delete it
    #   - No _check_permission() needed — delete from tools.py
    # ──────────────────────────────────────────────────────────
    # import psycopg2
    #
    # def get_connection(username: str = "default_user"):
    #     return psycopg2.connect(
    #         host     = os.getenv("REDSHIFT_HOST"),
    #         port     = int(os.getenv("REDSHIFT_PORT", 5439)),
    #         dbname   = os.getenv("REDSHIFT_DBNAME"),
    #         user     = username,
    #         password = os.getenv("REDSHIFT_PASSWORD")
    #         # Connects AS the actual user
    #         # Redshift enforces all permissions automatically
    #     )


    # ══════════════════════════════════════════════════════════
    # PRODUCTION REDSHIFT — SSO / IAM Authentication
    # ══════════════════════════════════════════════════════════
    # For corporate environments using Active Directory / Okta
    # Credentials are temporary (1 hour) — auto-rotate
    # More secure than password auth
    #
    # To activate:
    #   1. Comment out SQLite block above
    #   2. Uncomment this block
    #   3. pip install psycopg2-binary boto3
    #   4. Fill .env: REDSHIFT_HOST, REDSHIFT_PORT,
    #                 REDSHIFT_DBNAME, REDSHIFT_CLUSTER_ID,
    #                 AWS_REGION
    #   5. AUTH_MODE=sso in .env
    # ──────────────────────────────────────────────────────────
    # import boto3
    # import psycopg2
    #
    # def get_connection(username: str = "default_user"):
    #     # Step 1 — Get temporary credentials from AWS IAM
    #     client = boto3.client(
    #         "redshift",
    #         region_name = os.getenv("AWS_REGION", "us-east-1")
    #     )
    #     creds = client.get_cluster_credentials(
    #         DbUser            = username,
    #         DbName            = os.getenv("REDSHIFT_DBNAME"),
    #         ClusterIdentifier = os.getenv("REDSHIFT_CLUSTER_ID"),
    #         DurationSeconds   = 3600,   # 1 hour expiry
    #         AutoCreate        = False,
    #     )
    #     # Step 2 — Connect with temporary credentials
    #     return psycopg2.connect(
    #         host     = os.getenv("REDSHIFT_HOST"),
    #         port     = int(os.getenv("REDSHIFT_PORT", 5439)),
    #         dbname   = os.getenv("REDSHIFT_DBNAME"),
    #         user     = creds["DbUser"],
    #         password = creds["DbPassword"],
    #     )
    #     # Temporary credentials expire automatically after 1 hour
    #     # Much more secure than permanent passwords


def setup_sample_database():
    """
    Creates all tables including restricted employee_salaries.
    Simulates a real enterprise DB with mixed permission levels.

    Table permissions (simulated via USER_PERMISSIONS above):
      orders, customers, products, order_returns → all users
      employee_salaries                          → admin only

    In Redshift production the equivalent setup would be:
      CREATE TABLE orders (...);
      CREATE TABLE employee_salaries (...);
      GRANT SELECT ON orders, customers, products, order_returns
            TO default_user, analyst, admin;
      GRANT SELECT ON employee_salaries TO admin;
      -- default_user and analyst cannot see employee_salaries
    """
    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ── Standard accessible tables ────────────────────────────
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

    # ── RESTRICTED table — HR/Finance data ────────────────────
    # Only admin has access (see USER_PERMISSIONS above)
    # In Redshift: GRANT SELECT ON employee_salaries TO admin;
    # default_user and analyst have no GRANT → access denied
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_salaries (
            employee_id   INTEGER PRIMARY KEY,
            employee_name TEXT    NOT NULL,
            department    TEXT    NOT NULL,
            salary        REAL    NOT NULL,
            bonus         REAL    NOT NULL,
            hire_date     TEXT    NOT NULL
        )
    """)

    # ── Sample data ───────────────────────────────────────────
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
    cursor.executemany("INSERT OR IGNORE INTO employee_salaries VALUES (?,?,?,?,?,?)", [
        (1, "John Smith",   "Engineering", 120000.00, 15000.00, "2020-01-15"),
        (2, "Sarah Jones",  "Sales",        85000.00, 12000.00, "2019-03-22"),
        (3, "Mike Johnson", "HR",           75000.00,  8000.00, "2021-05-10"),
        (4, "Lisa Brown",   "Finance",      95000.00, 10000.00, "2018-08-01"),
        (5, "Tom Wilson",   "Engineering", 130000.00, 18000.00, "2017-11-15"),
    ])

    conn.commit()
    conn.close()
    print(f"✅ Sample database created at: {DB_PATH}")
    print(f"   ✅ orders, customers, products, order_returns (all users)")
    print(f"   🔒 employee_salaries (admin only — restricted)")


if __name__ == "__main__":
    setup_sample_database()
