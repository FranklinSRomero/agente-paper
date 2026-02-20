import logging

from fastapi import Depends, FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from .auth import MCP_AUTH_TOKEN, require_token
from .guardrails import GuardrailError
from .logging_conf import setup_logging
from .mysql_client import MySQLClient
from .product_search import ProductSearch
from .schema_introspect import SchemaIntrospector
from .tools_schema import RawSelectInput, SalesReportInput, SearchProductsInput, StockAlertsInput

setup_logging()
logger = logging.getLogger(__name__)

api = FastAPI(title="mcp_server")
mcp = FastMCP("mysql_tools")

_db = MySQLClient()
_introspect = SchemaIntrospector(_db)
_search = ProductSearch(_db)


@mcp.tool()
def schema_overview() -> dict:
    return _introspect.schema_overview()


@mcp.tool()
def map_product_schema() -> dict:
    return _introspect.map_product_schema()


@mcp.tool()
def search_products(
    texto: str | None = None,
    sku: str | None = None,
    barcode: str | None = None,
    categoria: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    limit: int = 20,
) -> dict:
    return _search.search_products(texto, sku, barcode, categoria, price_min, price_max, limit)


@mcp.tool()
def stock_alerts(threshold_mode: str = "low_stock", limit: int = 20) -> dict:
    return _search.stock_alerts(threshold_mode, limit)


@mcp.tool()
def raw_select_restricted(query_template_id: str, params: dict) -> dict:
    return _search.raw_select_restricted(query_template_id, params)


@mcp.tool()
def sales_report(
    days: int = 30,
    categoria: str | None = None,
    sku: str | None = None,
    channel: str | None = None,
    top_n: int = 10,
) -> dict:
    return _search.sales_report(days, categoria, sku, channel, top_n)


@api.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.middleware("http")
async def mcp_auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/mcp"):
        auth = request.headers.get("Authorization", "")
        if not MCP_AUTH_TOKEN:
            return JSONResponse(status_code=500, content={"error": "MCP_AUTH_TOKEN missing"})
        if auth != f"Bearer {MCP_AUTH_TOKEN}":
            return JSONResponse(status_code=403, content={"error": "invalid token"})
    return await call_next(request)


@api.post("/api/tool/schema_overview", dependencies=[Depends(require_token)])
def schema_overview_api() -> dict:
    return schema_overview()


@api.post("/api/tool/map_product_schema", dependencies=[Depends(require_token)])
def map_product_schema_api() -> dict:
    return map_product_schema()


@api.post("/api/tool/search_products", dependencies=[Depends(require_token)])
def search_products_api(payload: SearchProductsInput) -> dict:
    return search_products(**payload.model_dump())


@api.post("/api/tool/stock_alerts", dependencies=[Depends(require_token)])
def stock_alerts_api(payload: StockAlertsInput) -> dict:
    return stock_alerts(**payload.model_dump())


@api.post("/api/tool/raw_select_restricted", dependencies=[Depends(require_token)])
def raw_select_api(payload: RawSelectInput) -> dict:
    return raw_select_restricted(payload.query_template_id, payload.params)


@api.post("/api/tool/sales_report", dependencies=[Depends(require_token)])
def sales_report_api(payload: SalesReportInput) -> dict:
    return sales_report(**payload.model_dump())


@api.exception_handler(GuardrailError)
def handle_guardrails(_, exc: GuardrailError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@api.exception_handler(ValueError)
def handle_value_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@api.exception_handler(Exception)
def handle_unhandled(_, exc: Exception):
    logger.exception("unhandled_mcp_error")
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})


try:
    api.mount("/mcp", mcp.streamable_http_app())
except Exception as exc:
    logger.warning("could_not_mount_mcp_streamable_http: %s", exc)
