import sqlite3
import json

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE approvals (request_data TEXT)")

request_data = {
    "order": {
        "instrument": {"symbol": "NVDA", "securityType": "STK", "currency": "USD"},
        "side": "SELL",
        "quantity": 4.0,
        "orderType": "MKT",
        "clientOrderId": "auto-12345"
    }
}
conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps(request_data),))

query = """
SELECT * FROM approvals
    WHERE json_extract(request_data, '$.order.instrument.symbol') = ?
    AND json_extract(request_data, '$.order.instrument.securityType') = ?
    AND json_extract(request_data, '$.order.side') = ?
    AND json_extract(request_data, '$.order.quantity') = ?
    AND json_extract(request_data, '$.order.orderType') = ?
"""

params = ("NVDA", "STK", "SELL", 4, "MKT")
print(f"Match with int 4: {conn.execute(query, params).fetchone()}")

params2 = ("NVDA", "STK", "SELL", 4.0, "MKT")
print(f"Match with float 4.0: {conn.execute(query, params2).fetchone()}")
