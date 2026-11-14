"""A product page supplies default context; only access can prevent a scope read."""
from __future__ import annotations


class ProductScope:
    def __init__(self, engine, products: list[str] | None):
        self.e = engine
        self.products = None if products is None else list(dict.fromkeys(
            self.canonical(p) for p in products if p and p.strip()))
        self.tables = set(self.products or [])
        self.units = sorted({p.split(".", 1)[0] for p in self.tables if "." in p})

    def canonical(self, product: str) -> str:
        product = product.strip().lower()
        if "." not in product:
            for unit in self.e.UNIT_ORDER:
                if product.startswith(unit + "-"):
                    return unit + "." + product[len(unit) + 1:]
        return product

    def metadata(self) -> dict:
        return {} if self.products is None else {"products": self.products}

    def denied_units(self, principal: str, requested: list[str]) -> list[str]:
        named = set(self.units) | set(requested)
        return sorted((named & set(self.e.UNIT_ORDER)) - self.e.g.readable_units(principal))

    def check(self, principal: str, requested: list[str]) -> dict | None:
        if self.products is None:
            return None
        allowed = self.e.g.readable_units(principal)
        denied = self.denied_units(principal, requested)
        if denied:
            from kge.services.ask.engine.wording import Hidden
            reason = f"{principal} may not read {', '.join(denied)}. No warehouse was queried."
            hidden = Hidden.of(self.e, principal)
            return {"behaviour": "refuse", "denied_warehouses": denied,
                    "products": [p if p.split(".", 1)[0] in allowed else "(withheld)" for p in self.products],
                    "reason": hidden.withheld(reason),
                    "trace": [{"step": "access_check", "allowed": sorted(allowed), "denied": denied}]}
        return None

    def outside_graph(self) -> bool:
        """Every requested product is absent from the graph, as a portal-only card is."""
        if not self.products:
            return False
        try:
            tables = self.e.g.catalogue().tables
        except (AttributeError, TypeError):
            return False
        for product in self.products:
            unit, dot, table = product.partition(".")
            if not dot or table in set(tables.get(unit, ())):
                return False
        return True

    def portal_only(self) -> bool:
        """Every requested product is a published or declared portal-only card.

        An unknown table stays page context: it is outside the graph catalogue and
        still falls back to its unit. A document set or other portal-only card does not.
        """
        if not self.outside_graph():
            return False
        profile = getattr(self.e, "profile", None)
        if profile is None:
            return False
        try:
            from kge.compiler.catalogue_publish import portal_only_ids
            ids = portal_only_ids(profile)
        except (OSError, ValueError, TypeError, AttributeError):
            return False
        return all(product in ids for product in self.products)

    def contains(self, table: str) -> bool:
        return self.products is None or table.lower() in self.tables

    def missing(self, tables) -> list[str]:
        return sorted({table for table in tables if not self.contains(table)})

    def documents(self, answer: dict) -> dict:
        """Prefer page-unit sources when they answer, otherwise keep the global readable answer."""
        if self.products is None:
            return answer
        allowed = {d["id"] for d in self.e.g.document_metadata() if not d["unit"] or d["unit"] in self.units}
        from kge.services.ask.engine.question import parse
        terms = parse(answer["question"], self.e.term_names, self.e).terms_found if answer.get("question") else []
        cited = set(answer.get("cite") or []) | {c["doc"] for c in answer.get("citations") or []}
        for term in terms if cited else ():
            definers = {d["id"] for d in self.e.g.defining_docs(term)} & cited
            if definers and not definers & allowed:
                allowed.update(definers)
        out = dict(answer)
        for key in ("cite", "citations", "references", "cited_docs", "not_cited", "withheld", "superseded_not_cited",
                    "uncertified_definition_not_cited", "uncertified_context_not_cited", "not_ai_ready", "not_readable", "vector_hits"):
            if key not in out:
                continue
            out[key] = [item for item in out[key] or []
                        if (item.get("doc", item.get("id")) if isinstance(item, dict) else item) in allowed]
        if (answer.get("cite") or answer.get("citations")) and not out.get("cite") and not out.get("citations"):
            return answer
        if "citations" in out:
            out["citations"] = [{**c, "n": i} for i, c in enumerate(out["citations"], 1)]
        return out
