from app.engine import calculate_portfolio
from app.main import _demo_request


def test_customer_acceptance_example_totals():
    result = calculate_portfolio(_demo_request({
        "target_start": "2026-11-01",
        "target_end": "2027-02-28",
        "growth_factor": 1.8,
    }))
    assert result.reference_start.isoformat() == "2025-11-01"
    assert result.reference_end.isoformat() == "2026-02-28"
    assert result.summary.net_sales == 2000
    assert result.summary.forecast_demand == 3600
    assert result.summary.sellable_stock == 1700
    assert result.summary.production_need == 1900


def test_depot_allocation_matches_approved_order_type_logic():
    result = calculate_portfolio(_demo_request({"growth_factor": 1.8}))
    allocation = {row.depot: row.allocated_production for row in result.summary.depot_totals}
    assert allocation == {
        "Zalando Offerable": 1100,
        "Ekol": 700,
        "Paris Shop": 100,
    }
    assert sum(allocation.values()) == result.summary.production_need
    assert all(
        sum(item.allocated_production for item in row.depot_allocations) == row.production_need
        for row in result.lines
    )


def test_filters_recalculate_only_selected_product_type():
    result = calculate_portfolio(_demo_request({
        "product_types": ["Kürk"],
        "growth_factor": 1.8,
    }))
    assert result.summary.net_sales == 1200
    assert result.summary.product_size_count == 4
    assert all(row.product_type == "Kürk" for row in result.lines)


def test_priority_is_deterministic_and_ranked():
    request = _demo_request({"growth_factor": 1.8})
    request.inventory = [row for row in request.inventory if row.sku != "KK61450-BROWN-38"]
    result = calculate_portfolio(request)
    first = result.lines[0]
    assert first.sku == "KK61450-BROWN-38"
    assert first.priority_level == 1
    assert first.priority_rank == 1


def test_pre_data_floor_period_warns_without_legacy_mapping():
    result = calculate_portfolio(_demo_request({
        "target_start": "2026-01-01",
        "target_end": "2026-02-28",
    }))
    assert any("01.09.2025" in warning for warning in result.warnings)
