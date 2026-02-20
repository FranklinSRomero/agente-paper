from pydantic import BaseModel, Field


class SearchProductsInput(BaseModel):
    texto: str | None = None
    sku: str | None = None
    barcode: str | None = None
    categoria: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    limit: int = Field(default=20, ge=1, le=50)


class StockAlertsInput(BaseModel):
    threshold_mode: str = "low_stock"
    limit: int = Field(default=20, ge=1, le=50)


class RawSelectInput(BaseModel):
    query_template_id: str
    params: dict = Field(default_factory=dict)


class SalesReportInput(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
    categoria: str | None = None
    sku: str | None = None
    channel: str | None = None
    top_n: int = Field(default=10, ge=1, le=20)
