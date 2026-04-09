import sqlite3
import json

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE approvals (request_data TEXT)")

# Request trade approval json
request_data = {
    "order": {
        "instrument": {
            "symbol": "NVDA",
            "securityType": "STK",
            "currency": "USD"
        },
        "side": "SELL",
        "quantity": 4,
        "orderType": "MKT",
        "clientOrderId": "auto-12345"
    },
    "reason": "..."
}

conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps(request_data),))

# Now try to match with the params
params = ("NVDA", "STK", "SELL", 4.0, "MKT")

query = """
SELECT * FROM approvals
    WHERE json_extract(request_data, '$.order.instrument.symbol') = ?
    AND json_extract(request_data, '$.order.instrument.securityType') = ?
    AND json_extract(request_data, '$.order.side') = ?
    AND json_extract(request_data, '$.order.quantity') = ?
    AND json_extract(request_data, '$.order.orderType') = ?
"""

row = conn.execute(query, params).fetchone()
print(f"Match: {row}")

# Check what json_extract returns for quantity
q_val = conn.execute("SELECT json_extract(request_data, '$.order.quantity') FROM approvals").fetchone()[0]
print(f"quantity from json_extract: {q_val} (type: {type(q_val)})")
