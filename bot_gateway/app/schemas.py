from typing import Literal

from pydantic import BaseModel, Field


class RouterFilters(BaseModel):
    sku: str | None = None
    barcode: str | None = None
    texto: str | None = None
    categoria: str | None = None
    price_min: float | None = None
    price_max: float | None = None


class RouterDecision(BaseModel):
    intent: Literal[
        "buscar_producto",
        "detalle_producto",
        "alertas_stock",
        "insight",
        "ayuda",
        "otro",
    ]
    needs_db: bool
    needs_vision: bool
    filters: RouterFilters = Field(default_factory=RouterFilters)
    confidence: float = Field(ge=0, le=1)
    ask_clarification: str | None = None


class VisionResult(BaseModel):
    barcode: str | None = None
    qr_text: str | None = None
    sku_candidates: list[str] = Field(default_factory=list)
    ocr_text: str | None = None
