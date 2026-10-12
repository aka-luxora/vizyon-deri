"""Read-only NetSuite REST SuiteQL adapter for the approved planning scope."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests
from requests_oauthlib import OAuth1

from .schemas import (
    HistoricalSalesLine,
    NetSuitePlanRequest,
    PortfolioPlanningRequest,
    ProductVariant,
    SellableInventoryLine,
)


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _identifier(name: str, value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} güvenli bir NetSuite script ID değil: {value!r}")
    return value


def _integer(value: Any) -> int:
    return int(Decimal(str(value or 0)).copy_abs().quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class NetSuiteConfig:
    account_id: str
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str
    size_field: str
    color_field: str
    brand_field: str
    type_field: str
    status_field: str
    order_type_field: str
    page_size: int = 1000
    timeout_seconds: int = 45
    location_aliases: dict[str, str] | None = None

    @classmethod
    def from_env(cls) -> "NetSuiteConfig":
        aliases_raw = _env("NETSUITE_LOCATION_ALIASES")
        aliases = json.loads(aliases_raw) if aliases_raw else {
            "zalando offerable": "Zalando Offerable",
            "ekol": "Ekol",
            "paris shop": "Paris Shop",
            "paris": "Paris Shop",
        }
        return cls(
            account_id=_env("NETSUITE_ACCOUNT_ID"),
            consumer_key=_env("NETSUITE_CONSUMER_KEY"),
            consumer_secret=_env("NETSUITE_CONSUMER_SECRET"),
            token_id=_env("NETSUITE_TOKEN_ID"),
            token_secret=_env("NETSUITE_TOKEN_SECRET"),
            size_field=_env("NETSUITE_FIELD_SIZE"),
            color_field=_env("NETSUITE_FIELD_COLOR"),
            brand_field=_env("NETSUITE_FIELD_BRAND"),
            type_field=_env("NETSUITE_FIELD_TYPE"),
            status_field=_env("NETSUITE_FIELD_STATUS"),
            order_type_field=_env("NETSUITE_FIELD_ORDER_TYPE"),
            page_size=max(1, min(int(_env("NETSUITE_PAGE_SIZE") or 1000), 1000)),
            timeout_seconds=max(5, int(_env("NETSUITE_TIMEOUT_SECONDS") or 45)),
            location_aliases={str(key).casefold(): str(value) for key, value in aliases.items()},
        )

    def missing(self) -> list[str]:
        values = {
            "NETSUITE_ACCOUNT_ID": self.account_id,
            "NETSUITE_CONSUMER_KEY": self.consumer_key,
            "NETSUITE_CONSUMER_SECRET": self.consumer_secret,
            "NETSUITE_TOKEN_ID": self.token_id,
            "NETSUITE_TOKEN_SECRET": self.token_secret,
            "NETSUITE_FIELD_SIZE": self.size_field,
            "NETSUITE_FIELD_COLOR": self.color_field,
            "NETSUITE_FIELD_BRAND": self.brand_field,
            "NETSUITE_FIELD_TYPE": self.type_field,
            "NETSUITE_FIELD_STATUS": self.status_field,
            "NETSUITE_FIELD_ORDER_TYPE": self.order_type_field,
        }
        return [key for key, value in values.items() if not value]

    def validate(self) -> None:
        missing = self.missing()
        if missing:
            raise ValueError("Eksik NetSuite ayarları: " + ", ".join(missing))
        for name, value in (
            ("NETSUITE_FIELD_SIZE", self.size_field),
            ("NETSUITE_FIELD_COLOR", self.color_field),
            ("NETSUITE_FIELD_BRAND", self.brand_field),
            ("NETSUITE_FIELD_TYPE", self.type_field),
            ("NETSUITE_FIELD_STATUS", self.status_field),
            ("NETSUITE_FIELD_ORDER_TYPE", self.order_type_field),
        ):
            _identifier(name, value)

    @property
    def base_url(self) -> str:
        account = self.account_id.casefold().replace("_", "-")
        return f"https://{account}.suitetalk.api.netsuite.com"

    def public_status(self) -> dict[str, Any]:
        masked_account = None
        if self.account_id:
            masked_account = "••••" + self.account_id[-4:]
        return {
            "configured": not self.missing(),
            "account": masked_account,
            "authentication": "TBA OAuth 1.0 / HMAC-SHA256",
            "read_only": True,
            "page_size": self.page_size,
            "missing_settings": self.missing(),
            "mapped_fields": {
                "size": self.size_field or None, "color": self.color_field or None,
                "brand": self.brand_field or None, "type": self.type_field or None,
                "status": self.status_field or None, "order_type": self.order_type_field or None,
            },
        }


class NetSuiteClient:
    def __init__(self, config: NetSuiteConfig, session: requests.Session | None = None):
        config.validate()
        self.config = config
        self.session = session or requests.Session()
        self.session.auth = OAuth1(
            config.consumer_key,
            client_secret=config.consumer_secret,
            resource_owner_key=config.token_id,
            resource_owner_secret=config.token_secret,
            realm=config.account_id,
            signature_method="HMAC-SHA256",
        )

    def query(self, suiteql: str) -> list[dict[str, Any]]:
        url = f"{self.config.base_url}/services/rest/query/v1/suiteql"
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = None
            for attempt in range(4):
                response = self.session.post(
                    url,
                    params={"limit": self.config.page_size, "offset": offset},
                    headers={"Prefer": "transient", "Content-Type": "application/json", "Accept": "application/json"},
                    json={"q": suiteql},
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                retry_after = response.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else min(8, 2 ** attempt))
            if response is None or not response.ok:
                status = response.status_code if response is not None else "no-response"
                detail = response.text[:500] if response is not None else "NetSuite yanıt vermedi."
                raise RuntimeError(f"NetSuite SuiteQL hatası ({status}): {detail}")
            payload = response.json()
            page = payload.get("items", [])
            rows.extend(page)
            if not payload.get("hasMore") or not page:
                return rows
            offset += len(page)

    def connection_test(self) -> dict[str, Any]:
        started = time.perf_counter()
        rows = self.query("SELECT id FROM item WHERE ROWNUM <= 1")
        return {
            "connected": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "sample_row_count": len(rows),
        }


class NetSuiteAdapter:
    def __init__(self, client: NetSuiteClient):
        self.client = client
        self.config = client.config

    def _item_query(self) -> str:
        c = self.config
        return f"""
            SELECT i.id AS item_id, i.itemid AS sku,
                   COALESCE(i.displayname, i.itemid) AS product,
                   BUILTIN.DF(i.{c.size_field}) AS size_code,
                   BUILTIN.DF(i.{c.color_field}) AS color_code,
                   BUILTIN.DF(i.{c.brand_field}) AS brand,
                   BUILTIN.DF(i.{c.type_field}) AS product_type,
                   BUILTIN.DF(i.{c.status_field}) AS product_status
            FROM item i
            WHERE i.itemtype IN ('InvtPart', 'Assembly') AND i.isinactive = 'F'
            ORDER BY i.id
        """

    def _sales_query(self, start: date, end: date) -> str:
        field = self.config.order_type_field
        return f"""
            SELECT t.id AS transaction_id, tl.uniquekey AS line_key,
                   TO_CHAR(t.trandate, 'YYYY-MM-DD') AS transaction_date,
                   t.type AS transaction_type, tl.item AS item_id,
                   ABS(tl.quantity) AS quantity,
                   COALESCE(BUILTIN.DF(t.{field}), BUILTIN.DF(origin.{field})) AS order_type,
                   BUILTIN.DF(tl.location) AS fulfillment_location
            FROM transaction t
            JOIN transactionline tl ON tl.transaction = t.id
            LEFT JOIN transaction origin ON origin.id = t.createdfrom
            WHERE t.type IN ('CustInvc', 'CustCred')
              AND NVL(t.voided, 'F') = 'F' AND tl.mainline = 'F'
              AND tl.taxline = 'F' AND tl.item IS NOT NULL
              AND t.trandate BETWEEN TO_DATE('{start.isoformat()}', 'YYYY-MM-DD')
                                 AND TO_DATE('{end.isoformat()}', 'YYYY-MM-DD')
            ORDER BY t.id, tl.uniquekey
        """

    @staticmethod
    def _inventory_query() -> str:
        return """
            SELECT ia.item AS item_id, BUILTIN.DF(ia.location) AS location_name,
                   ia.quantityavailable AS available_qty
            FROM inventoryitemlocations ia
            JOIN item i ON i.id = ia.item
            WHERE i.itemtype IN ('InvtPart', 'Assembly') AND i.isinactive = 'F'
            ORDER BY ia.item, ia.location
        """

    def _depot(self, location: str) -> str | None:
        normalized = " ".join(location.casefold().split())
        aliases = self.config.location_aliases or {}
        if normalized in aliases:
            return aliases[normalized]
        for source, target in aliases.items():
            if source in normalized:
                return target
        return None

    def fetch(self, plan: NetSuitePlanRequest) -> tuple[PortfolioPlanningRequest, dict[str, Any]]:
        try:
            reference_start = plan.policy.target_start.replace(year=plan.policy.target_start.year - 1)
        except ValueError:
            reference_start = plan.policy.target_start.replace(year=plan.policy.target_start.year - 1, day=28)
        try:
            reference_end = plan.policy.target_end.replace(year=plan.policy.target_end.year - 1)
        except ValueError:
            reference_end = plan.policy.target_end.replace(year=plan.policy.target_end.year - 1, day=28)
        item_rows = self.client.query(self._item_query())
        sales_rows = self.client.query(self._sales_query(reference_start, reference_end))
        inventory_rows = self.client.query(self._inventory_query())

        products: list[ProductVariant] = []
        sku_by_id: dict[str, str] = {}
        rejected_items = 0
        for row in item_rows:
            required = [row.get("sku"), row.get("product"), row.get("size_code"), row.get("brand"),
                        row.get("product_type"), row.get("product_status")]
            if not all(value is not None and str(value).strip() for value in required):
                rejected_items += 1
                continue
            sku = str(row["sku"]).strip()
            sku_by_id[str(row["item_id"])] = sku
            products.append(ProductVariant(
                sku=sku, product=str(row["product"]).strip(), size=str(row["size_code"]).strip(),
                color=str(row.get("color_code") or "").strip() or None,
                brand=str(row["brand"]).strip(), product_type=str(row["product_type"]).strip(),
                status=str(row["product_status"]).strip(),
            ))

        sales: list[HistoricalSalesLine] = []
        rejected_sales = 0
        for row in sales_rows:
            sku = sku_by_id.get(str(row.get("item_id")))
            order_type = str(row.get("order_type") or "").strip()
            if not sku or not order_type:
                rejected_sales += 1
                continue
            quantity = _integer(row.get("quantity"))
            is_return = str(row.get("transaction_type")) == "CustCred"
            sales.append(HistoricalSalesLine(
                sku=sku, transaction_date=date.fromisoformat(str(row["transaction_date"])[:10]),
                gross_sales=0 if is_return else quantity, returns=quantity if is_return else 0,
                order_type=order_type,
                fulfillment_location=str(row.get("fulfillment_location") or "").strip() or None,
            ))

        inventory: list[SellableInventoryLine] = []
        rejected_inventory = 0
        for row in inventory_rows:
            sku = sku_by_id.get(str(row.get("item_id")))
            depot = self._depot(str(row.get("location_name") or ""))
            if not sku or not depot:
                rejected_inventory += 1
                continue
            inventory.append(SellableInventoryLine(
                sku=sku, depot=depot, sellable_quantity=max(0, _integer(row.get("available_qty"))),
            ))

        request = PortfolioPlanningRequest(
            plan_name=plan.plan_name, filters=plan.filters, policy=plan.policy,
            products=products, sales=sales, inventory=inventory,
        )
        diagnostics = {
            "source_counts": {"items": len(item_rows), "sales_returns": len(sales_rows), "inventory": len(inventory_rows)},
            "accepted_counts": {"products": len(products), "sales_returns": len(sales), "inventory": len(inventory)},
            "rejected_counts": {"items": rejected_items, "sales_returns": rejected_sales, "inventory": rejected_inventory},
            "reference_start": reference_start.isoformat(), "reference_end": reference_end.isoformat(),
        }
        return request, diagnostics
