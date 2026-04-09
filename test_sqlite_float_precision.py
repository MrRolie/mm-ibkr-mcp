import sqlite3
import json

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE approvals (request_data TEXT)")

conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps({"order": {"quantity": 4.0}}),))

print(conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = 4").fetchone())
print(conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = 4.0").fetchone())

# Now with a float like 4.1
conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps({"order": {"quantity": 4.1}}),))
print(conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = 4.1").fetchone())

# In python, parameters are passed as bindings:
print(conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = ?", (4.1,)).fetchone())
print(conn.execute("SELECT * FROM approvals WHERE json_extract(request_data, '$.order.quantity') = CAST(? AS REAL)", (4.1,)).fetchone())
