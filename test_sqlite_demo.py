import sqlite3
import json

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE approvals (request_data TEXT)")

# Agent calls request_trade_approval with quantity=4 (int) or 4.0 (float)
order_data = {
    "instrument": {"symbol": "NVDA", "securityType": "STK", "currency": "USD"},
    "side": "SELL",
    "quantity": 4, # let's say the dump produces int 4 if it came in as int
    "orderType": "MKT",
    "clientOrderId": "auto-12345"
}
req_data = {"order": order_data, "reason": "demo"}
conn.execute("INSERT INTO approvals VALUES (?)", (json.dumps(req_data),))

# Agent calls place_order
# order_params comes from order.model_dump()
order_params = {
    "instrument": {"symbol": "NVDA", "securityType": "STK", "currency": "USD"},
    "side": "SELL",
    "quantity": 4, # wait, let's test if pydantic gives float
    "orderType": "MKT"
}

symbol = order_params.get("instrument", {}).get("symbol")
sec_type = order_params.get("instrument", {}).get("securityType")
side = order_params.get("side")
quantity = order_params.get("quantity")
order_type = order_params.get("orderType")

print(f"quantity={quantity} type={type(quantity)}")

# find_approved_trade_by_order_params converts to float
query_qty = float(quantity)

q = """
SELECT * FROM approvals 
WHERE json_extract(request_data, '$.order.instrument.symbol') = ?
  AND json_extract(request_data, '$.order.instrument.securityType') = ?
  AND json_extract(request_data, '$.order.side') = ?
  AND json_extract(request_data, '$.order.quantity') = ?
  AND json_extract(request_data, '$.order.orderType') = ?
"""

res = conn.execute(q, (symbol, sec_type, side, query_qty, order_type)).fetchone()
print(f"Match: {res}")
