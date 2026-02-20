from .mysql_client import MySQLClient


class SchemaIntrospector:
    def __init__(self, db: MySQLClient):
        self.db = db

    def schema_overview(self) -> dict:
        tables = self.db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() LIMIT 200"
        )
        return {"tables": tables}

    def map_product_schema(self) -> dict:
        columns = self.db.query(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            "AND (column_name LIKE '%sku%' "
            "OR column_name LIKE '%bar%' "
            "OR column_name LIKE '%price%' "
            "OR column_name LIKE '%stock%' "
            "OR column_name LIKE '%name%' "
            "OR column_name LIKE '%product%' "
            "OR column_name LIKE '%categoria%') "
            "LIMIT 200"
        )
        grouped: dict[str, dict] = {}
        for row in columns:
            t = row["table_name"]
            c = row["column_name"].lower()
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
            if not bucket["sku_col"] and "sku" in c:
                bucket["sku_col"] = row["column_name"]
                bucket["score"] += 2
            if not bucket["barcode_col"] and any(k in c for k in ["barcode", "ean", "upc", "gtin"]):
                bucket["barcode_col"] = row["column_name"]
                bucket["score"] += 2
            if not bucket["name_col"] and any(k in c for k in ["name", "nombre", "product"]):
                bucket["name_col"] = row["column_name"]
                bucket["score"] += 1
            if not bucket["category_col"] and any(k in c for k in ["category", "categoria"]):
                bucket["category_col"] = row["column_name"]
                bucket["score"] += 1
            if not bucket["price_col"] and "price" in c:
                bucket["price_col"] = row["column_name"]
                bucket["score"] += 1
            if not bucket["stock_col"] and any(k in c for k in ["stock", "qty", "quantity", "invent"]):
                bucket["stock_col"] = row["column_name"]
                bucket["score"] += 1
        ranked = sorted(grouped.values(), key=lambda x: x["score"], reverse=True)
        return {"candidates": columns, "ranked_tables": ranked}
