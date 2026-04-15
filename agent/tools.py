"""
agent/tools.py — 11 SQL tools for database exploration

Tools:
  Existing (6): get_all_tables, get_ddl, get_record_count,
                get_table_owner, run_select_query, get_column_info
  New     (5): run_join_query, run_aggregation, explain_query,
                get_table_stats, search_schema
"""

from agent.database import get_connection


# ── 1. List all tables ────────────────────────────────────────
def get_all_tables(username: str = "default_user") -> str:
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, type FROM sqlite_master
            WHERE type='table' ORDER BY name
        """)
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return "No tables found."
        result = "Available tables:\n"
        for row in rows:
            result += f"  - {row['name']} ({row['type']})\n"
        return result
    except Exception as e:
        return f"Error fetching tables: {str(e)}"


# ── 2. Get DDL ────────────────────────────────────────────────
def get_ddl(table_name: str, username: str = "default_user") -> str:
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name.lower(),)
        )
        if not cursor.fetchone():
            return f"Table '{table_name}' not found."
        cursor.execute(f"PRAGMA table_info({table_name})")
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
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name.lower(),)
        )
        if not cursor.fetchone():
            return f"Table '{table_name}' not found."
        conn.close()
        return f"Table '{table_name}' is owned by: {username} (database administrator)"
    except Exception as e:
        return f"Error fetching owner: {str(e)}"


# ── 5. Run SELECT query ───────────────────────────────────────
def run_select_query(query: str, username: str = "default_user") -> str:
    clean   = query.strip().upper()
    blocked = ["INSERT","UPDATE","DELETE","DROP","CREATE",
               "ALTER","TRUNCATE","GRANT","REVOKE"]
    for kw in blocked:
        if clean.startswith(kw):
            return f"Access denied: '{kw}' not allowed. SELECT only."
    if not clean.startswith("SELECT"):
        return "Only SELECT queries are allowed."
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
        return f"Query error: {str(e)}"


# ── 6. Column info ────────────────────────────────────────────
def get_column_info(table_name: str, username: str = "default_user") -> str:
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        conn.close()
        if not columns:
            return f"Table '{table_name}' not found."
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


# ── 7. JOIN query (NEW) ───────────────────────────────────────
def run_join_query(query: str, username: str = "default_user") -> str:
    """
    Executes multi-table JOIN queries.
    Validates it is a SELECT with JOIN keyword.
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
        return "This tool is for JOIN queries. Use run_select for single-table queries."
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
        return f"JOIN query error: {str(e)}"


# ── 8. Aggregation query (NEW) ────────────────────────────────
def run_aggregation(query: str, username: str = "default_user") -> str:
    """
    Executes aggregation queries — SUM, AVG, COUNT, GROUP BY, HAVING.
    Validates SELECT-only.
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
        return f"Aggregation error: {str(e)}"


# ── 9. Explain query (NEW) ────────────────────────────────────
def explain_query(query: str, username: str = "default_user") -> str:
    """
    Explains what a SQL query does — execution plan + description.
    Uses EXPLAIN QUERY PLAN in SQLite (equivalent to EXPLAIN in Redshift).
    """
    clean = query.strip().upper()
    if not clean.startswith("SELECT"):
        return "Only SELECT queries can be explained."
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(f"EXPLAIN QUERY PLAN {query}")
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return "No execution plan available."
        result  = f"Execution plan for query:\n{query}\n\n"
        result += "Plan steps:\n"
        result += "-" * 60 + "\n"
        for row in rows:
            result += f"  Step {row[0]}: {row[3]}\n"
        result += "\nRedshift equivalent: EXPLAIN <your_query>"
        return result
    except Exception as e:
        return f"Explain error: {str(e)}"


# ── 10. Table statistics (NEW) ────────────────────────────────
def get_table_stats(table_name: str, username: str = "default_user") -> str:
    """
    Returns column-level statistics: min, max, avg, null count.
    Useful for data profiling and quality checks.
    """
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
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

            # Only compute numeric stats for numeric columns
            if any(t in col_type for t in ["INT","REAL","FLOAT","NUMERIC","DECIMAL"]):
                cursor.execute(f"""
                    SELECT
                        MIN({col_name})   as min_val,
                        MAX({col_name})   as max_val,
                        AVG({col_name})   as avg_val,
                        SUM(CASE WHEN {col_name} IS NULL THEN 1 ELSE 0 END) as null_count
                    FROM {table_name}
                """)
                stats = cursor.fetchone()
                min_v  = f"{stats['min_val']:.2f}"  if stats['min_val']  is not None else "N/A"
                max_v  = f"{stats['max_val']:.2f}"  if stats['max_val']  is not None else "N/A"
                avg_v  = f"{stats['avg_val']:.2f}"  if stats['avg_val']  is not None else "N/A"
                nulls  = stats['null_count']
            else:
                cursor.execute(f"""
                    SELECT
                        MIN({col_name})  as min_val,
                        MAX({col_name})  as max_val,
                        SUM(CASE WHEN {col_name} IS NULL THEN 1 ELSE 0 END) as null_count
                    FROM {table_name}
                """)
                stats = cursor.fetchone()
                min_v = str(stats['min_val'])[:12] if stats['min_val'] else "N/A"
                max_v = str(stats['max_val'])[:12] if stats['max_val'] else "N/A"
                avg_v = "N/A"
                nulls = stats['null_count']

            result += f"{col_name:<20} {col_type:<12} {min_v:<15} {max_v:<15} {avg_v:<15} {nulls}\n"

        conn.close()
        return result
    except Exception as e:
        return f"Error fetching stats: {str(e)}"


# ── 11. Search schema (NEW) ───────────────────────────────────
def search_schema(keyword: str, username: str = "default_user") -> str:
    """
    Searches for tables and columns containing a keyword.
    Extremely useful when exploring an unfamiliar database.
    """
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables  = [row["name"] for row in cursor.fetchall()]
        kw_low  = keyword.lower()
        matches = []

        for table in tables:
            # Match table name
            if kw_low in table.lower():
                matches.append(f"  📋 TABLE: {table}  (table name matches '{keyword}')")

            # Match column names
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            for col in columns:
                if kw_low in col["name"].lower():
                    matches.append(
                        f"  🔹 COLUMN: {table}.{col['name']} ({col['type']})  "
                        f"(column name matches '{keyword}')"
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
