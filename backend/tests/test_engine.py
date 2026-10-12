from app.engine import calculate_plan
from app.schemas import AccessRule, PlanningRequest


def sample_request():
    return PlanningRequest.model_validate({
        "scenario_name": "Q4 VSP", "sku": "VSP-COAT-38", "product": "VSP Coat", "size": "38",
        "policy": {
            "reference_start": "2025-09-01", "reference_end": "2025-12-31",
            "plan_start": "2026-10-01", "plan_end": "2026-12-31",
            "growth_factor": 1.05, "safety_stock": 3,
            "production_multiple": 6, "minimum_production_quantity": 12,
        },
        "sales": [
            {"channel": "Zalando", "gross_sales": 91, "returns": 59},
            {"channel": "Shopify", "gross_sales": 40, "returns": 4},
        ],
        "inventory": [
            {"location": "Zalando Depo", "offerable": 14},
            {"location": "Ekol", "offerable": 10},
        ],
        "future_supply": [
            {"location": "Zalando Depo", "quantity": 5, "expected_date": "2026-10-15", "source": "inbound"},
            {"location": "Ekol", "quantity": 100, "expected_date": "2027-01-10", "source": "production"},
        ],
        "access_rules": [
            {"channel": "Zalando", "location": "Zalando Depo", "priority": 1},
            {"channel": "Shopify", "location": "Ekol", "priority": 1},
        ],
    })


def test_explainable_plan_and_date_bound_supply():
    result = calculate_plan(sample_request())
    # 122 reference days, 92 plan days: Zalando 26; Shopify 29 forecasts.
    assert result.forecast_demand == 55
    assert result.usable_future_supply == 5
    assert result.calculated_need == 29  # 55 - (14 + 10 + 5) + safety 3
    assert result.recommended_production == 30
    assert sum(x.quantity for x in result.allocation) == 30
    assert len(result.explanation) == 5


def test_stock_is_not_double_counted_between_channels():
    req = sample_request()
    req.inventory = [req.inventory[1]]
    req.future_supply = []
    req.access_rules.append(AccessRule(channel="Zalando", location="Ekol", priority=2))
    result = calculate_plan(req)
    assert sum(c.allocated_existing_supply for c in result.channels) == 10


def test_location_minimum_stock_is_protected():
    req = sample_request()
    req.sales = [req.sales[1]]
    req.future_supply = []
    req.access_rules = [AccessRule(channel="Shopify", location="Ekol", priority=1, minimum_stock=4)]
    result = calculate_plan(req)
    assert result.channels[0].allocated_existing_supply == 6
