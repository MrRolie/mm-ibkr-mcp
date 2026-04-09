from pydantic import BaseModel, Field
class O(BaseModel):
    quantity: float = Field(...)
print(O(quantity=4).model_dump(mode="json"))
