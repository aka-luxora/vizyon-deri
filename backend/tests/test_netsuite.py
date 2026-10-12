from datetime import date

import pytest

from app.engine import calculate_portfolio
from app.netsuite import NetSuiteAdapter, NetSuiteClient, NetSuiteConfig
from app.schemas import NetSuitePlanRequest


def config(**overrides):
    values = dict(
        account_id="123456_SB1", consumer_key="consumer", consumer_secret="secret",
        token_id="token", token_secret="token-secret", size_field="custitem_size",
        color_field="custitem_color", brand_field="custitem_brand",
        type_field="custitem_type", status_field="custitem_status",
        order_type_field="custbody_order_type",
        location_aliases={
            "zalando offerable": "Zalando Offerable", "ekol": "Ekol", "paris": "Paris Shop"
        },
    )
    values.update(overrides)
    return NetSuiteConfig(**values)


class FakeClient:
    def __init__(self):
        self.config = config()
        self.queries = []

    def query(self, sql):
        self.queries.append(sql)
        if "inventoryitemlocations" in sql:
            return [
                {"item_id": "1", "location_name": "Zalando Offerable", "available_qty": "1"},
                {"item_id": "1", "location_name": "Ekol Warehouse", "available_qty": "1"},
                {"item_id": "1", "location_name": "Paris", "available_qty": "1"},
                {"item_id": "1", "location_name": "Other", "available_qty": "99"},
            ]
        if "transactionline" in sql:
            return [
                {"transaction_id": "10", "line_key": "1", "transaction_date": "2026-01-10",
                 "transaction_type": "CustInvc", "item_id": "1", "quantity": "10",
                 "order_type": "Zalando Fulfilled", "fulfillment_location": "Zalando Offerable"},
                {"transaction_id": "11", "line_key": "1", "transaction_date": "2026-01-20",
                 "transaction_type": "CustCred", "item_id": "1", "quantity": "2",
                 "order_type": "Zalando Fulfilled", "fulfillment_location": "Zalando Offerable"},
            ]
        return [{
            "item_id": "1", "sku": "SKU-36", "product": "Test Coat", "size_code": "36",
            "color_code": "Black", "brand": "VSP", "product_type": "Kürk", "product_status": "S1",
        }]


def plan_request():
    return NetSuitePlanRequest.model_validate({
        "plan_name": "Canlı veri testi",
        "filters": {"product_types": ["Kürk"], "statuses": ["S1"], "brands": ["VSP"]},
        "policy": {"target_start": "2027-01-01", "target_end": "2027-01-31", "growth_factor": 2},
    })


def test_adapter_transforms_read_only_netsuite_rows_to_plan_contract():
    client = FakeClient()
    request, diagnostics = NetSuiteAdapter(client).fetch(plan_request())
    result = calculate_portfolio(request)

    assert diagnostics["reference_start"] == "2026-01-01"
    assert diagnostics["rejected_counts"]["inventory"] == 1
    assert result.summary.gross_sales == 10
    assert result.summary.returns == 2
    assert result.summary.net_sales == 8
    assert result.summary.forecast_demand == 16
    assert result.summary.sellable_stock == 3
    assert result.summary.production_need == 13
    assert "TO_DATE('2026-01-01'" in client.queries[1]


def test_script_id_validation_rejects_sql_injection():
    bad = config(size_field="custitem_size; DROP TABLE item")
    with pytest.raises(ValueError, match="script ID"):
        bad.validate()


def test_account_specific_domain_is_normalized():
    assert config().base_url == "https://123456-sb1.suitetalk.api.netsuite.com"


def test_oauth_client_can_be_constructed_with_hmac_sha256():
    client = NetSuiteClient(config())
    assert client.session.auth is not None
