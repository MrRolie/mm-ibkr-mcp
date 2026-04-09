from ibkr_core.models import OrderSpec, SymbolSpec

order = OrderSpec(
    instrument=SymbolSpec(symbol="NVDA", securityType="STK"),
    side="SELL",
    quantity=4,
    orderType="MKT",
    clientOrderId="nvda-sell-4-0409"
)

d = order.model_dump(mode="json", exclude_none=True)
print(d)
