import re

FORBIDDEN_TOKENS = [
    " insert ",
    " update ",
    " delete ",
    " drop ",
    " alter ",
    " create ",
    " truncate ",
    " grant ",
    " revoke ",
]


class GuardrailError(ValueError):
    pass


def validate_readonly_sql(query: str) -> str:
    q = " " + query.strip().lower() + " "
    if not q.strip().startswith("select"):
        raise GuardrailError("only SELECT allowed")
    if ";" in q:
        raise GuardrailError("multi-statement not allowed")
    if "--" in q or "/*" in q or "*/" in q or "#" in q:
        raise GuardrailError("sql comments not allowed")
    for token in FORBIDDEN_TOKENS:
        if token in q:
            raise GuardrailError(f"forbidden token: {token.strip()}")
    if " limit " not in q:
        raise GuardrailError("LIMIT required")
    m = re.search(r"\blimit\s+(\d+)\b", q)
    if m and int(m.group(1)) > 200:
        raise GuardrailError("LIMIT too high (max 200)")
    return query
