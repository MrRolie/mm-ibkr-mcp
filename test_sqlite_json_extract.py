import sqlite3
import json

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE approvals (request_data TEXT)")
conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps({"order": {"quantity": 4}}),))

row = conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = ?", (4.0,)).fetchone()
print(f"Match with float 4.0: {row}")

row = conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = ?", (4,)).fetchone()
print(f"Match with int 4: {row}")

row = conn.execute("SELECT * FROM approvals WHERE CAST(json_extract(request_data, '$.order.quantity') AS REAL) = ?", (4.0,)).fetchone()
print(f"Match with cast to REAL 4.0: {row}")
