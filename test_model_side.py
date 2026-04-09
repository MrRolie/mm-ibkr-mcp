from ibkr_core.models import OrderSpec, SymbolSpec
try:
    OrderSpec(
        instrument=SymbolSpec(symbol="NVDA", securityType="STK"),
        side="sell",
        quantity=4,
        orderType="MKT",
        clientOrderId="123"
    )
except Exception as e:
    print(e)
