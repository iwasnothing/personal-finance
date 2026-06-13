import duckdb

class DatabaseManager:
    def __init__(self, db_path="transactions.duckdb"):
        self.conn = duckdb.connect(db_path)
        self.init_db()

    def init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT,
                date DATE,
                type TEXT,
                amount REAL,
                category TEXT
            )
        """)

    def insert_transactions(self, transactions):
        if not transactions:
            return
        self.conn.executemany(
            "INSERT INTO transactions (source_file, date, type, amount, category) VALUES (?, ?, ?, ?, ?)",
            transactions
        )

    def get_all_transactions(self):
        return self.conn.execute("SELECT * FROM transactions").fetchall()
