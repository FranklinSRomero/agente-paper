import os

from sqlalchemy import create_engine, text

from .guardrails import validate_readonly_sql


class MySQLClient:
    def __init__(self):
        user = os.getenv("MYSQL_USER", "")
        pwd = os.getenv("MYSQL_PASSWORD", "")
        host = os.getenv("MYSQL_HOST", "mysql")
        port = int(os.getenv("MYSQL_PORT", "3306"))
        db = os.getenv("MYSQL_DATABASE", "")
        ssl_disabled = os.getenv("MYSQL_SSL_DISABLED", "true").lower() == "true"
        connect_args = {
            "connect_timeout": 5,
            "read_timeout": 10,
            "write_timeout": 10,
        }
        if ssl_disabled:
            connect_args["ssl_disabled"] = True
        self.engine = create_engine(
            f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}",
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=connect_args,
        )

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        validated = validate_readonly_sql(sql)
        with self.engine.connect() as conn:
            rows = conn.execute(text(validated), params or {})
            return [dict(r._mapping) for r in rows]
