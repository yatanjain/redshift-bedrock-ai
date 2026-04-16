"""
agent/tools.py — 11 SQL tools with permission enforcement

╔══════════════════════════════════════════════════════════════╗
║  CURRENT MODE : SQLite (POC)                                 ║
║  Permissions enforced via _check_permission() and            ║
║  get_allowed_tables() from database.py                       ║
║                                                              ║
║  PROD MODE    : Amazon Redshift (see comments below)         ║
║  When switching to Redshift:                                  ║
║    1. DELETE _check_permission() — not needed                ║
║    2. DELETE permission checks from every tool               ║
║    3. DELETE get_allowed_tables import                       ║
║    4. UPDATE SQL queries (see REDSHIFT SQL comments)         ║
║    5. Redshift enforces permissions automatically            ║
╚══════════════════════════════════════════════════════════════╝
"""

from agent.database import get_connection, get_allowed_tables


# ── Permission check helper (SQLite POC only) ─────────────────
# DELETE THIS ENTIRE FUNCTION when switching to Redshift
# Redshift handles this automatically at DB level
def _check_permission(table_name: str, username: str):
    """
    POC only — checks USER_PERMISSIONS dict in database.py.
    Redshift production: DELETE THIS — DB enforces natively.
    """
    allowed = get_allowed_tables(username)
    if table_name.lower() not in [t.lower() for t in allowed]:
        return (
            f"❌ Access denied: You don't have permission to access "
            f"table '{table_name}'.\n"
            f"Your accessible tables are: {', '.join(allowed)}"
        )
    return None


# ── 1. List all tables ────────────────────────────────────────
def get_all_tables(username: str = "default_user") -> str:
    """
    Returns ONLY tables the user has permission to access.

    SQLite POC:
      Filters results against USER_PERMISSIONS dict

    Redshift Production:
      DELETE the filtering logic below
      information_schema.tables automatically returns
      only tables this user has SELECT permission on
      Replace query with:
        SELECT table_name, 'table' as type
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """
    try:
        allowed       = get_allowed_tables(username)
        allowed_lower = [t.lower() for t in allowed]

        conn   = get_connection(username)
        cursor = conn.cursor()

        # SQLite query
        cursor.execute("""
            SELECT name, type FROM sqlite_master
            WHERE type='table' ORDER BY name
        """)
        # ── Redshift replacement (uncomment for production) ───
        # cursor.execute("""
        #     SELECT table_name as name, 'table' as type
        #     FROM information_schema.tables
        #     WHERE table_schema = 'public'
        #     ORDER BY table_name
        # """)
        # No filtering needed — Redshift returns only accessible tables

        all_rows = cursor.fetchall()
        conn.close()

        # SQLite POC — filter by allowed tables
        # DELETE these 2 lines for Redshift (not needed)
        rows = [r for r in all_rows if r["name"].lower() in allowed_lower]

        if not rows:
            return "No tables found."

        result = f"Available tables (you have access to {len(rows)} tables):\n"
        for row in rows:
            result += f"  - {row['name']} ({row['type']})\n"
        return result

    except Exception as e:
        return f"Error fetching tables: {str(e)}"


# ── 2. Get DDL ────────────────────────────────────────────────
def get_ddl(table_name: str, username: str = "default_user") -> str:
    """
    Returns CREATE TABLE definition for a table.

    SQLite POC:   Uses PRAGMA table_info()
    Redshift Prod: Replace PRAGMA with information_schema query:
      SELECT column_name, data_type, is_nullable,
             character_maximum_length, column_default
      FROM information_schema.columns
      WHERE table_name = %s AND table_schema = 'public'
      ORDER BY ordinal_position
    """
    # SQLite permission check — DELETE for Redshift
    err = _check_permission(table_name, username)
    if err:
        return err

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()

        # SQLite — check table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name.lower(),)
        )
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT table_name FROM information_schema.tables
        #     WHERE table_schema = 'public' AND table_name = %s
        # """, (table_name.lower(),))

        if not cursor.fetchone():
            return f"Table '{table_name}' not found or access denied."

        # SQLite — get columns
        cursor.execute(f"PRAGMA table_info({table_name})")
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT column_name as name, data_type as type,
        #            CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END as notnull,
        #            column_default as dflt_value,
        #            CASE WHEN constraint_type = 'PRIMARY KEY' THEN 1 ELSE 0 END as pk
        #     FROM information_schema.columns c
        #     LEFT JOIN information_schema.key_column_usage k
        #       ON c.column_name = k.column_name
        #       AND c.table_name = k.table_name
        #     LEFT JOIN information_schema.table_constraints tc
        #       ON k.constraint_name = tc.constraint_name
        #     WHERE c.table_name = %s AND c.table_schema = 'public'
        #     ORDER BY ordinal_position
        # """, (table_name.lower(),))

        columns = cursor.fetchall()
        conn.close()

        ddl      = f"CREATE TABLE {table_name} (\n"
        col_defs = []
        for col in columns:
            pk       = " PRIMARY KEY" if col["pk"] else ""
            nullable = "" if col["notnull"] else " NULL"
            default  = f" DEFAULT {col['dflt_value']}" if col["dflt_value"] else ""
            col_defs.append(f"    {col['name']}  {col['type']}{pk}{nullable}{default}")
        ddl += ",\n".join(col_defs) + "\n);"
        return ddl

    except Exception as e:
        return f"Error fetching DDL: {str(e)}"


# ── 3. Record count ───────────────────────────────────────────
def get_record_count(table_name: str, username: str = "default_user") -> str:
    """
    SQLite + Redshift: Same query works for both.
    Only difference: remove _check_permission() for Redshift.
    """
    # DELETE for Redshift
    err = _check_permission(table_name, username)
    if err:
        return err

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) as total FROM {table_name}")
        result = cursor.fetchone()
        conn.close()
        return f"Table '{table_name}' has {result['total']:,} records."
    except Exception as e:
        return f"Error counting records: {str(e)}"


# ── 4. Table owner ────────────────────────────────────────────
def get_table_owner(table_name: str, username: str = "default_user") -> str:
    """
    SQLite POC:   Simulates owner as username
    Redshift Prod: Use pg_tables to get real owner:
      SELECT tableowner FROM pg_tables
      WHERE tablename = %s AND schemaname = 'public'
    """
    # DELETE for Redshift
    err = _check_permission(table_name, username)
    if err:
        return err

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()

        # SQLite check
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name.lower(),)
        )
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT tableowner FROM pg_tables
        #     WHERE tablename = %s AND schemaname = 'public'
        # """, (table_name.lower(),))
        # row = cursor.fetchone()
        # return f"Table '{table_name}' is owned by: {row['tableowner']}"

        if not cursor.fetchone():
            return f"Table '{table_name}' not found or access denied."
        conn.close()
        return f"Table '{table_name}' is owned by: {username} (database administrator)"

    except Exception as e:
        return f"Error fetching owner: {str(e)}"


# ── 5. Run SELECT ─────────────────────────────────────────────
def run_select_query(query: str, username: str = "default_user") -> str:
    """
    Executes SELECT query with security validation.

    SQLite POC:
      Parses table names from query → checks permissions
    Redshift Prod:
      Remove table parsing and permission check
      Redshift raises psycopg2.Error if user lacks permission
      The except block handles it gracefully
    """
    clean   = query.strip().upper()
    blocked = ["INSERT","UPDATE","DELETE","DROP","CREATE",
               "ALTER","TRUNCATE","GRANT","REVOKE"]
    for kw in blocked:
        if clean.startswith(kw):
            return f"Access denied: '{kw}' not allowed. SELECT only."
    if not clean.startswith("SELECT"):
        return "Only SELECT queries are allowed."

    # SQLite POC — parse and check table permissions
    # DELETE this block for Redshift
    allowed       = get_allowed_tables(username)
    allowed_lower = [t.lower() for t in allowed]
    words = query.lower().replace(",", " ").replace(";", " ").split()
    for i, word in enumerate(words):
        if word in ("from", "join") and i + 1 < len(words):
            tbl = words[i + 1].strip()
            if tbl and tbl not in allowed_lower:
                return (
                    f"❌ Access denied: You don't have permission to access "
                    f"table '{tbl}'.\n"
                    f"Your accessible tables are: {', '.join(allowed)}"
                )

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchmany(50)
        conn.close()

        if not rows:
            return "Query returned no results."

        cols   = [d[0] for d in cursor.description]
        result = " | ".join(cols) + "\n" + "-" * 60 + "\n"
        for row in rows:
            result += " | ".join(str(v) for v in row) + "\n"
        total   = len(rows)
        result += f"\n({total} row{'s' if total!=1 else ''} returned)"
        if total == 50:
            result += " — limited to 50 rows"
        return result

    except Exception as e:
        # In Redshift this catches permission denied errors natively
        # e.g. "permission denied for relation employee_salaries"
        err_msg = str(e)
        if "permission denied" in err_msg.lower():
            return f"❌ Access denied: {err_msg}"
        return f"Query error: {err_msg}"


# ── 6. Column info ────────────────────────────────────────────
def get_column_info(table_name: str, username: str = "default_user") -> str:
    """
    SQLite POC:   PRAGMA table_info()
    Redshift Prod: information_schema.columns
      SELECT column_name, data_type, is_nullable, column_default
      FROM information_schema.columns
      WHERE table_name = %s AND table_schema = 'public'
      ORDER BY ordinal_position
    """
    # DELETE for Redshift
    err = _check_permission(table_name, username)
    if err:
        return err

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()

        # SQLite
        cursor.execute(f"PRAGMA table_info({table_name})")
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT column_name as name, data_type as type,
        #            CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END as notnull,
        #            column_default as dflt_value, 0 as pk
        #     FROM information_schema.columns
        #     WHERE table_name = %s AND table_schema = 'public'
        #     ORDER BY ordinal_position
        # """, (table_name.lower(),))

        columns = cursor.fetchall()
        conn.close()

        if not columns:
            return f"Table '{table_name}' not found or access denied."

        result  = f"Column metadata for '{table_name}':\n\n"
        result += f"{'Column':<20} {'Type':<15} {'Nullable':<10} {'Default':<15} {'PK'}\n"
        result += "-" * 70 + "\n"
        for col in columns:
            nullable = "NO"  if col["notnull"] else "YES"
            default  = str(col["dflt_value"]) if col["dflt_value"] else "None"
            pk       = "YES" if col["pk"] else ""
            result  += f"{col['name']:<20} {col['type']:<15} {nullable:<10} {default:<15} {pk}\n"
        return result

    except Exception as e:
        return f"Error fetching columns: {str(e)}"


# ── 7. JOIN query ─────────────────────────────────────────────
def run_join_query(query: str, username: str = "default_user") -> str:
    """
    Executes multi-table JOIN query.
    SQLite + Redshift: Same SQL syntax works for both.
    Only difference: remove permission parsing for Redshift.
    """
    clean   = query.strip().upper()
    blocked = ["INSERT","UPDATE","DELETE","DROP","CREATE",
               "ALTER","TRUNCATE","GRANT","REVOKE"]
    for kw in blocked:
        if clean.startswith(kw):
            return f"Access denied: '{kw}' not allowed."
    if not clean.startswith("SELECT"):
        return "Only SELECT queries are allowed."
    if "JOIN" not in clean:
        return "This tool is for JOIN queries."

    # SQLite POC — permission check on all tables
    # DELETE this block for Redshift
    allowed       = get_allowed_tables(username)
    allowed_lower = [t.lower() for t in allowed]
    words = query.lower().replace(",", " ").replace(";", " ").split()
    for i, word in enumerate(words):
        if word in ("from", "join") and i + 1 < len(words):
            tbl = words[i + 1].strip()
            if tbl and tbl not in allowed_lower:
                return (
                    f"❌ Access denied: You don't have permission to access "
                    f"table '{tbl}'.\n"
                    f"Your accessible tables are: {', '.join(allowed)}"
                )

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchmany(50)
        conn.close()

        if not rows:
            return "JOIN query returned no results."

        cols   = [d[0] for d in cursor.description]
        result = " | ".join(cols) + "\n" + "-" * 80 + "\n"
        for row in rows:
            result += " | ".join(str(v) for v in row) + "\n"
        total   = len(rows)
        result += f"\n({total} row{'s' if total!=1 else ''} returned)"
        return result

    except Exception as e:
        if "permission denied" in str(e).lower():
            return f"❌ Access denied: {str(e)}"
        return f"JOIN query error: {str(e)}"


# ── 8. Aggregation ────────────────────────────────────────────
def run_aggregation(query: str, username: str = "default_user") -> str:
    """
    Executes aggregation queries — GROUP BY, SUM, AVG, COUNT.
    SQLite + Redshift: Same SQL syntax works for both.
    Only difference: remove permission parsing for Redshift.
    """
    clean   = query.strip().upper()
    blocked = ["INSERT","UPDATE","DELETE","DROP","CREATE",
               "ALTER","TRUNCATE","GRANT","REVOKE"]
    for kw in blocked:
        if clean.startswith(kw):
            return f"Access denied: '{kw}' not allowed."
    if not clean.startswith("SELECT"):
        return "Only SELECT queries are allowed."

    agg_keywords = ["SUM(","AVG(","COUNT(","MIN(","MAX(","GROUP BY","HAVING"]
    if not any(kw in clean for kw in agg_keywords):
        return "This tool is for aggregation queries (SUM, AVG, COUNT, GROUP BY, HAVING)."

    # SQLite POC — permission check
    # DELETE this block for Redshift
    allowed       = get_allowed_tables(username)
    allowed_lower = [t.lower() for t in allowed]
    words = query.lower().replace(",", " ").replace(";", " ").split()
    for i, word in enumerate(words):
        if word in ("from", "join") and i + 1 < len(words):
            tbl = words[i + 1].strip()
            if tbl and tbl not in allowed_lower:
                return (
                    f"❌ Access denied: You don't have permission to access "
                    f"table '{tbl}'.\n"
                    f"Your accessible tables are: {', '.join(allowed)}"
                )

    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "Aggregation returned no results."

        cols   = [d[0] for d in cursor.description]
        result = " | ".join(cols) + "\n" + "-" * 60 + "\n"
        for row in rows:
            result += " | ".join(str(v) for v in row) + "\n"
        result += f"\n({len(rows)} row{'s' if len(rows)!=1 else ''} returned)"
        return result

    except Exception as e:
        if "permission denied" in str(e).lower():
            return f"❌ Access denied: {str(e)}"
        return f"Aggregation error: {str(e)}"


# ── 9. Explain query ──────────────────────────────────────────
def explain_query(query: str, username: str = "default_user") -> str:
    """
    SQLite POC:   EXPLAIN QUERY PLAN
    Redshift Prod: EXPLAIN (no QUERY PLAN keyword)
      Replace: cursor.execute(f"EXPLAIN QUERY PLAN {query}")
      With:    cursor.execute(f"EXPLAIN {query}")
    """
    clean = query.strip().upper()
    if not clean.startswith("SELECT"):
        return "Only SELECT queries can be explained."
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()

        # SQLite
        cursor.execute(f"EXPLAIN QUERY PLAN {query}")
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute(f"EXPLAIN {query}")

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "No execution plan available."

        result  = f"Execution plan for:\n{query}\n\n"
        result += "Plan steps:\n" + "-" * 60 + "\n"
        for row in rows:
            result += f"  Step {row[0]}: {row[3]}\n"
        result += "\nRedshift equivalent: EXPLAIN <your_query>"
        return result

    except Exception as e:
        return f"Explain error: {str(e)}"


# ── 10. Table stats ───────────────────────────────────────────
def get_table_stats(table_name: str, username: str = "default_user") -> str:
    """
    Column-level statistics: min, max, avg, nulls.
    SQLite + Redshift: Same SQL works for both.
    Only difference: remove _check_permission() for Redshift.
    """
    # DELETE for Redshift
    err = _check_permission(table_name, username)
    if err:
        return err

    try:
        conn    = get_connection(username)
        cursor  = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT column_name as name, data_type as type
        #     FROM information_schema.columns
        #     WHERE table_name = %s AND table_schema = 'public'
        #     ORDER BY ordinal_position
        # """, (table_name.lower(),))

        columns = cursor.fetchall()
        if not columns:
            conn.close()
            return f"Table '{table_name}' not found."

        result  = f"Column statistics for '{table_name}':\n\n"
        result += f"{'Column':<20} {'Type':<12} {'Min':<15} {'Max':<15} {'Avg':<15} {'Nulls'}\n"
        result += "-" * 90 + "\n"

        for col in columns:
            col_name = col["name"]
            col_type = col["type"].upper()

            if any(t in col_type for t in ["INT","REAL","FLOAT","NUMERIC","DECIMAL"]):
                cursor.execute(f"""
                    SELECT MIN({col_name}) as mn, MAX({col_name}) as mx,
                           AVG({col_name}) as av,
                           SUM(CASE WHEN {col_name} IS NULL THEN 1 ELSE 0 END) as nc
                    FROM {table_name}
                """)
                s     = cursor.fetchone()
                min_v = f"{s['mn']:.2f}" if s['mn'] is not None else "N/A"
                max_v = f"{s['mx']:.2f}" if s['mx'] is not None else "N/A"
                avg_v = f"{s['av']:.2f}" if s['av'] is not None else "N/A"
                nulls = s['nc']
            else:
                cursor.execute(f"""
                    SELECT MIN({col_name}) as mn, MAX({col_name}) as mx,
                           SUM(CASE WHEN {col_name} IS NULL THEN 1 ELSE 0 END) as nc
                    FROM {table_name}
                """)
                s     = cursor.fetchone()
                min_v = str(s['mn'])[:12] if s['mn'] else "N/A"
                max_v = str(s['mx'])[:12] if s['mx'] else "N/A"
                avg_v = "N/A"
                nulls = s['nc']

            result += f"{col_name:<20} {col_type:<12} {min_v:<15} {max_v:<15} {avg_v:<15} {nulls}\n"

        conn.close()
        return result

    except Exception as e:
        return f"Error fetching stats: {str(e)}"


# ── 11. Search schema ─────────────────────────────────────────
def search_schema(keyword: str, username: str = "default_user") -> str:
    """
    Searches tables and columns by keyword.
    Only searches within tables user can access.

    SQLite POC:
      Filters against USER_PERMISSIONS
    Redshift Prod:
      information_schema.columns only returns accessible tables
      No filtering needed — replace sqlite_master with:
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' ORDER BY table_name
    """
    try:
        conn    = get_connection(username)
        cursor  = conn.cursor()
        allowed = get_allowed_tables(username)
        allowed_lower = [t.lower() for t in allowed]

        # SQLite — get tables and filter
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        # ── Redshift replacement ──────────────────────────────
        # cursor.execute("""
        #     SELECT table_name as name
        #     FROM information_schema.tables
        #     WHERE table_schema = 'public' ORDER BY table_name
        # """)
        # No filtering needed — Redshift returns only accessible tables

        # SQLite POC — filter by permissions
        # DELETE for Redshift
        all_tables = [
            row["name"] for row in cursor.fetchall()
            if row["name"].lower() in allowed_lower
        ]

        kw_low  = keyword.lower()
        matches = []

        for table in all_tables:
            if kw_low in table.lower():
                matches.append(f"  📋 TABLE: {table}  (matches '{keyword}')")

            cursor.execute(f"PRAGMA table_info({table})")
            # ── Redshift replacement ──────────────────────────
            # cursor.execute("""
            #     SELECT column_name as name, data_type as type
            #     FROM information_schema.columns
            #     WHERE table_name = %s AND table_schema = 'public'
            # """, (table,))

            columns = cursor.fetchall()
            for col in columns:
                if kw_low in col["name"].lower():
                    matches.append(
                        f"  🔹 COLUMN: {table}.{col['name']} ({col['type']})"
                    )

        conn.close()

        if not matches:
            return f"No tables or columns found matching '{keyword}'."

        result  = f"Schema search results for '{keyword}':\n\n"
        result += "\n".join(matches)
        result += f"\n\n({len(matches)} match{'es' if len(matches)!=1 else ''} found)"
        return result

    except Exception as e:
        return f"Schema search error: {str(e)}"
