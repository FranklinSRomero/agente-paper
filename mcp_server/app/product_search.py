import os
import re

from .mysql_client import MySQLClient


class ProductSearch:
    def __init__(self, db: MySQLClient):
        self.db = db

    def _discover_mapping(self) -> dict:
        sql = (
            "SELECT table_name, column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() LIMIT 200"
        )
        rows = self.db.query(sql)
        grouped: dict[str, dict] = {}
        for row in rows:
            row_norm = {str(k).lower(): v for k, v in row.items()}
            t = str(row_norm.get("table_name", ""))
            c = str(row_norm.get("column_name", ""))
            if not t or not c:
                continue
            cl = c.lower()
            bucket = grouped.setdefault(
                t,
                {
                    "table": t,
                    "sku_col": None,
                    "barcode_col": None,
                    "name_col": None,
                    "category_col": None,
                    "price_col": None,
                    "stock_col": None,
                    "score": 0,
                },
            )
            if not bucket["sku_col"] and "sku" in cl:
                bucket["sku_col"] = c
                bucket["score"] += 2
            if not bucket["barcode_col"] and any(k in cl for k in ["barcode", "ean", "upc", "gtin"]):
                bucket["barcode_col"] = c
                bucket["score"] += 2
            if not bucket["name_col"] and any(k in cl for k in ["name", "nombre", "product"]):
                bucket["name_col"] = c
                bucket["score"] += 1
            if not bucket["category_col"] and any(k in cl for k in ["category", "categoria"]):
                bucket["category_col"] = c
                bucket["score"] += 1
            if not bucket["price_col"] and "price" in cl:
                bucket["price_col"] = c
                bucket["score"] += 1
            if not bucket["stock_col"] and any(k in cl for k in ["stock", "qty", "quantity", "invent"]):
                bucket["stock_col"] = c
                bucket["score"] += 1
        # Prefer explicit table when integrating existing DB dumps.
        preferred_table = os.getenv("MCP_PRODUCT_TABLE", "").strip()
        if preferred_table and preferred_table in grouped:
            mapping = grouped[preferred_table]
            columns_sql = (
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :table "
                "LIMIT 200"
            )
            col_rows = self.db.query(columns_sql, {"table": preferred_table})
            cols = {str(next(iter(r.values()))).lower() for r in col_rows if r}

            def pick(*candidates: str) -> str | None:
                for cand in candidates:
                    if cand.lower() in cols:
                        return cand
                return None

            mapping["sku_col"] = mapping["sku_col"] or pick("sku", "reference", "code", "id")
            mapping["barcode_col"] = mapping["barcode_col"] or pick("barcode", "ean", "upc", "gtin", "code", "reference")
            mapping["name_col"] = mapping["name_col"] or pick("product_name", "name", "nombre", "product")
            mapping["category_col"] = mapping["category_col"] or pick("categoria", "category", "taxcat")
            mapping["price_col"] = mapping["price_col"] or pick("pricesell", "price", "unit_price", "pricebuy")
            mapping["stock_col"] = mapping["stock_col"] or pick("stock", "stockunits", "qty", "quantity", "invent")
            return mapping

        # Improve selection for common POS schemas where product codes are "reference"/"code".
        for bucket in grouped.values():
            table_l = str(bucket["table"]).lower()
            if table_l == "products":
                if not bucket["sku_col"]:
                    bucket["sku_col"] = "reference"
                    bucket["score"] += 2
                if not bucket["barcode_col"]:
                    bucket["barcode_col"] = "code"
                    bucket["score"] += 2
                if not bucket["name_col"]:
                    bucket["name_col"] = "name"
                    bucket["score"] += 1
                if not bucket["category_col"]:
                    bucket["category_col"] = "category"
                    bucket["score"] += 1
                if not bucket["price_col"]:
                    bucket["price_col"] = "pricesell"
                    bucket["score"] += 1
                if not bucket["stock_col"]:
                    bucket["stock_col"] = "stockunits"
                    bucket["score"] += 1
                # Bias to choose the real imported catalog over tiny demo tables.
                bucket["score"] += 4
            if table_l == "products_catalog":
                # Keep available but with lower priority when richer schemas exist.
                bucket["score"] -= 2

        ranked = sorted(grouped.values(), key=lambda x: x["score"], reverse=True)
        if not ranked or ranked[0]["score"] < 2:
            raise ValueError("No se encontro tabla candidata de productos por introspeccion")
        return ranked[0]

    def search_products(
        self,
        texto: str | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        categoria: str | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        limit: int = 20,
    ) -> dict:
        limit = min(max(limit, 1), 50)
        mapping = self._discover_mapping()
        table = mapping["table"]
        sku_col = mapping["sku_col"] or "sku"
        barcode_col = mapping["barcode_col"] or "barcode"
        name_col = mapping["name_col"] or sku_col
        category_col = mapping["category_col"] or name_col
        price_col = mapping["price_col"]
        table_l = str(table).lower()
        base_alias = "p"
        from_clause = f"FROM `{table}` {base_alias}"
        select_clause = f"SELECT {base_alias}.*"

        if table_l == "products":
            # For legacy POS schema, expose human-readable category names.
            from_clause += f" LEFT JOIN `categories` c ON c.id = {base_alias}.`{category_col}`"
            select_clause += ", c.name AS category_name"

        text_columns = [
            f"{base_alias}.`{name_col}`",
            f"{base_alias}.`{sku_col}`",
            f"{base_alias}.`{barcode_col}`",
            f"{base_alias}.`{category_col}`",
        ]
        if table_l == "products":
            text_columns.append("c.name")

        sql = (
            f"{select_clause} {from_clause} "
            f"WHERE (:sku IS NULL OR {base_alias}.`{sku_col}` = :sku) "
            f"AND (:barcode IS NULL OR {base_alias}.`{barcode_col}` = :barcode) "
            f"AND (:categoria IS NULL OR {base_alias}.`{category_col}` = :categoria"
        )
        if table_l == "products":
            sql += " OR c.name = :categoria"
        sql += ") "
        # Global text search:
        # 1) phrase-like match over key columns
        # 2) tokenized match (all tokens must appear in at least one searchable column)
        sql += "AND (:texto IS NULL OR ("
        phrase_like = " OR ".join(f"{col} LIKE CONCAT('%', :texto, '%')" for col in text_columns)
        sql += f"({phrase_like})"
        params_extra: dict[str, str] = {}
        text_tokens = []
        if texto:
            text_tokens = [tok for tok in re.split(r"\s+", str(texto).strip()) if tok][:6]
        if text_tokens:
            token_groups = []
            for i, _ in enumerate(text_tokens):
                token_key = f"texto_tok_{i}"
                params_extra[token_key] = text_tokens[i]
                token_like = " OR ".join(f"{col} LIKE CONCAT('%', :{token_key}, '%')" for col in text_columns)
                token_groups.append(f"({token_like})")
            sql += " OR (" + " AND ".join(token_groups) + ")"
        sql += ")) "
        if price_col:
            sql += (
                f"AND (:price_min IS NULL OR {base_alias}.`{price_col}` >= :price_min) "
                f"AND (:price_max IS NULL OR {base_alias}.`{price_col}` <= :price_max) "
            )
        sql += "LIMIT :limit"
        params = {
            "texto": texto,
            "sku": sku,
            "barcode": barcode,
            "categoria": categoria,
            "price_min": price_min,
            "price_max": price_max,
            "limit": limit,
        }
        params.update(params_extra)
        rows = self.db.query(sql, params)
        return {"count": len(rows), "items": rows}

    def stock_alerts(self, threshold_mode: str = "low_stock", limit: int = 20) -> dict:
        limit = min(max(limit, 1), 50)
        mapping = self._discover_mapping()
        table = mapping["table"]
        stock_col = mapping["stock_col"] or "stock"
        if threshold_mode == "out_of_stock":
            sql = f"SELECT * FROM `{table}` WHERE `{stock_col}` = 0 LIMIT :limit"
        else:
            sql = f"SELECT * FROM `{table}` WHERE `{stock_col}` <= 5 LIMIT :limit"
        rows = self.db.query(sql, {"limit": limit})
        return {"count": len(rows), "items": rows}

    def raw_select_restricted(self, query_template_id: str, params: dict) -> dict:
        mapping = self._discover_mapping()
        table = mapping["table"]
        sku_col = mapping["sku_col"] or "sku"
        name_col = mapping["name_col"] or sku_col
        price_col = mapping["price_col"] or sku_col
        stock_col = mapping["stock_col"] or sku_col
        templates = {
            "top_expensive": (
                f"SELECT `{sku_col}` AS sku, `{name_col}` AS name, `{price_col}` AS price "
                f"FROM `{table}` ORDER BY `{price_col}` DESC LIMIT :limit"
            ),
            "recent_stock_low": (
                f"SELECT `{sku_col}` AS sku, `{name_col}` AS name, `{stock_col}` AS stock "
                f"FROM `{table}` WHERE `{stock_col}` <= :threshold LIMIT :limit"
            ),
        }
        if query_template_id not in templates:
            raise ValueError("template not allowed")
        sql = templates[query_template_id]
        safe_params = {
            "limit": min(max(int(params.get("limit", 20)), 1), 50),
            "threshold": min(max(int(params.get("threshold", 5)), 0), 1000),
        }
        rows = self.db.query(sql, safe_params)
        return {"count": len(rows), "items": rows}

    def sales_report(
        self,
        days: int = 30,
        categoria: str | None = None,
        sku: str | None = None,
        channel: str | None = None,
        top_n: int = 10,
    ) -> dict:
        days = min(max(days, 1), 90)
        top_n = min(max(top_n, 1), 20)
        params = {
            "days": days,
            "categoria": categoria,
            "sku": sku,
            "channel": channel,
            "top_n": top_n,
        }

        summary_sql = (
            "SELECT "
            "COUNT(*) AS tx_count, "
            "COALESCE(SUM(quantity), 0) AS units_net, "
            "COALESCE(SUM(CASE WHEN quantity > 0 THEN quantity ELSE 0 END), 0) AS units_sold, "
            "COALESCE(SUM(CASE WHEN quantity < 0 THEN -quantity ELSE 0 END), 0) AS units_returned, "
            "ROUND(COALESCE(SUM(net_amount), 0), 2) AS net_sales, "
            "ROUND(COALESCE(SUM(CASE WHEN net_amount > 0 THEN net_amount ELSE 0 END), 0), 2) AS gross_sales "
            "FROM sales_transactions "
            "WHERE sale_date >= DATE_SUB(CURDATE(), INTERVAL :days DAY) "
            "AND (:categoria IS NULL OR categoria = :categoria) "
            "AND (:sku IS NULL OR sku = :sku) "
            "AND (:channel IS NULL OR sales_channel = :channel) "
            "LIMIT 1"
        )
        daily_sql = (
            "SELECT "
            "sale_date, "
            "ROUND(COALESCE(SUM(net_amount), 0), 2) AS net_sales, "
            "COALESCE(SUM(quantity), 0) AS units_net "
            "FROM sales_transactions "
            "WHERE sale_date >= DATE_SUB(CURDATE(), INTERVAL :days DAY) "
            "AND (:categoria IS NULL OR categoria = :categoria) "
            "AND (:sku IS NULL OR sku = :sku) "
            "AND (:channel IS NULL OR sales_channel = :channel) "
            "GROUP BY sale_date "
            "ORDER BY sale_date ASC "
            "LIMIT 200"
        )
        by_category_sql = (
            "SELECT "
            "categoria, "
            "ROUND(COALESCE(SUM(net_amount), 0), 2) AS net_sales, "
            "COALESCE(SUM(quantity), 0) AS units_net "
            "FROM sales_transactions "
            "WHERE sale_date >= DATE_SUB(CURDATE(), INTERVAL :days DAY) "
            "AND (:categoria IS NULL OR categoria = :categoria) "
            "AND (:sku IS NULL OR sku = :sku) "
            "AND (:channel IS NULL OR sales_channel = :channel) "
            "GROUP BY categoria "
            "ORDER BY net_sales DESC "
            "LIMIT 20"
        )
        top_products_sql = (
            "SELECT "
            "sku, "
            "product_name, "
            "categoria, "
            "ROUND(COALESCE(SUM(net_amount), 0), 2) AS net_sales, "
            "COALESCE(SUM(quantity), 0) AS units_net "
            "FROM sales_transactions "
            "WHERE sale_date >= DATE_SUB(CURDATE(), INTERVAL :days DAY) "
            "AND (:categoria IS NULL OR categoria = :categoria) "
            "AND (:sku IS NULL OR sku = :sku) "
            "AND (:channel IS NULL OR sales_channel = :channel) "
            "GROUP BY sku, product_name, categoria "
            "ORDER BY net_sales DESC "
            "LIMIT :top_n"
        )

        summary_rows = self.db.query(summary_sql, params)
        summary = summary_rows[0] if summary_rows else {
            "tx_count": 0,
            "units_net": 0,
            "units_sold": 0,
            "units_returned": 0,
            "net_sales": 0.0,
            "gross_sales": 0.0,
        }
        daily = self.db.query(daily_sql, params)
        by_category = self.db.query(by_category_sql, params)
        top_products = self.db.query(top_products_sql, params)
        return {
            "window_days": days,
            "filters": {"categoria": categoria, "sku": sku, "channel": channel},
            "summary": summary,
            "daily_series": daily,
            "category_breakdown": by_category,
            "top_products": top_products,
            "chart_ready": {
                "x": [str(r["sale_date"]) for r in daily],
                "y_net_sales": [float(r["net_sales"]) for r in daily],
                "y_units_net": [int(r["units_net"]) for r in daily],
            },
        }
