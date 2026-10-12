from collections import defaultdict
from datetime import date
from math import ceil

from .schemas import (
    AllocationResult,
    ChannelResult,
    DepotAllocation,
    DepotTotal,
    PlanningRequest,
    PlanningResult,
    PortfolioLineResult,
    PortfolioPlanningRequest,
    PortfolioPlanningResult,
    PortfolioSummary,
)


DEPOTS = ("Zalando Offerable", "Ekol", "Paris Shop")
DATA_FLOOR = date(2025, 9, 1)


def _round_production(need: int, minimum: int, multiple: int) -> int:
    if need <= 0:
        return 0
    target = max(need, minimum)
    return ceil(target / multiple) * multiple


def calculate_plan(request: PlanningRequest) -> PlanningResult:
    """Calculate one SKU/size plan without hidden statistical assumptions.

    Existing supply is assigned once across channels, preventing the same stock
    from being counted for both Shopify and a marketplace.
    """
    policy = request.policy
    reference_days = (policy.reference_end - policy.reference_start).days + 1
    planning_days = (policy.plan_end - policy.plan_start).days + 1

    inventory_by_location = {
        row.location: max(0, row.offerable - row.committed - row.blocked)
        for row in request.inventory
    }
    future_by_location: dict[str, int] = defaultdict(int)
    for row in request.future_supply:
        if row.expected_date <= policy.plan_end:
            future_by_location[row.location] += row.quantity

    rules_by_channel: dict[str, list] = defaultdict(list)
    for rule in request.access_rules:
        if rule.can_access:
            rules_by_channel[rule.channel].append(rule)
    for rules in rules_by_channel.values():
        rules.sort(key=lambda r: (r.priority, r.location))

    sales_by_channel: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    for row in request.sales:
        gross, returned = sales_by_channel[row.channel]
        sales_by_channel[row.channel] = gross + row.gross_sales, returned + row.returns

    # Highest net forecast is served first; deterministic alphabetical tie-break.
    demand_rows = []
    for channel, (gross, returned) in sales_by_channel.items():
        net = gross - returned
        base = net / reference_days * planning_days
        forecast = ceil(base * policy.growth_factor)
        demand_rows.append((channel, gross, returned, net, base, forecast))
    demand_rows.sort(key=lambda row: (-row[5], row[0]))

    remaining_current = dict(inventory_by_location)
    remaining_future = dict(future_by_location)
    channel_results: list[ChannelResult] = []
    channel_unmet: dict[str, int] = {}

    for channel, gross, returned, net, base, forecast in demand_rows:
        rules = rules_by_channel.get(channel, [])
        accessible_current = sum(max(0, remaining_current.get(r.location, 0) - r.minimum_stock) for r in rules)
        accessible_future = sum(remaining_future.get(r.location, 0) for r in rules)
        outstanding = forecast
        allocated = 0
        for rule in rules:
            available = max(0, remaining_current.get(rule.location, 0) - rule.minimum_stock)
            take = min(outstanding, available)
            remaining_current[rule.location] = remaining_current.get(rule.location, 0) - take
            outstanding -= take
            allocated += take
            if outstanding == 0:
                break
        for rule in rules:
            take = min(outstanding, remaining_future.get(rule.location, 0))
            remaining_future[rule.location] = remaining_future.get(rule.location, 0) - take
            outstanding -= take
            allocated += take
            if outstanding == 0:
                break
        channel_unmet[channel] = outstanding
        channel_results.append(ChannelResult(
            channel=channel, gross_sales=gross, returns=returned,
            return_rate=(returned / gross if gross else 0), net_sales=net,
            base_demand=round(base, 2), forecast_demand=forecast,
            accessible_current_stock=accessible_current,
            accessible_future_supply=accessible_future,
            allocated_existing_supply=allocated, unmet_demand=outstanding,
        ))

    forecast_total = sum(row[5] for row in demand_rows)
    gross_total = sum(row[1] for row in demand_rows)
    returns_total = sum(row[2] for row in demand_rows)
    net_total = gross_total - returns_total
    unmet_total = sum(channel_unmet.values())
    calculated_need = max(0, unmet_total + policy.safety_stock)
    recommended = _round_production(
        calculated_need, policy.minimum_production_quantity, policy.production_multiple
    )

    allocation: list[AllocationResult] = []
    production_left = recommended
    for channel, *_ in demand_rows:
        channel_need = channel_unmet[channel]
        for rule in rules_by_channel.get(channel, []):
            quantity = min(channel_need, production_left)
            if quantity:
                allocation.append(AllocationResult(
                    channel=channel, location=rule.location, quantity=quantity,
                    reason=f"{channel} erişim önceliği {rule.priority}; karşılanmamış kanal talebi",
                ))
                channel_need -= quantity
                production_left -= quantity
            if channel_need == 0:
                break
    if production_left:
        # MOQ/multiple rounding surplus goes to the strongest accessible channel.
        for channel, *_ in demand_rows:
            rules = rules_by_channel.get(channel, [])
            if rules:
                allocation.append(AllocationResult(
                    channel=channel, location=rules[0].location, quantity=production_left,
                    reason="Üretim katı/MOQ kaynaklı fazla; en yüksek talep önceliği",
                ))
                production_left = 0
                break

    usable_current = sum(inventory_by_location.values())
    usable_future = sum(future_by_location.values())
    explanation = [
        f"Referans: {reference_days} gün; plan: {planning_days} gün.",
        f"Net satış {gross_total} brüt - {returns_total} iade = {net_total} adet.",
        f"Kanal bazında dönem normalizasyonu ve {policy.growth_factor:.2f} büyüme ile talep {forecast_total} adet.",
        f"Erişim matrisiyle tekilleştirilmiş arz sonrası açık {unmet_total}; güvenlik stoğu {policy.safety_stock}.",
        f"İhtiyaç {calculated_need}; MOQ {policy.minimum_production_quantity} ve kat {policy.production_multiple} sonrası öneri {recommended}.",
    ]
    return PlanningResult(
        scenario_name=request.scenario_name, sku=request.sku, product=request.product,
        size=request.size, reference_days=reference_days, planning_days=planning_days,
        gross_sales=gross_total, returns=returns_total,
        return_rate=(returns_total / gross_total if gross_total else 0), net_sales=net_total,
        growth_factor=policy.growth_factor, forecast_demand=forecast_total,
        safety_stock=policy.safety_stock, usable_current_stock=usable_current,
        usable_future_supply=usable_future, calculated_need=calculated_need,
        recommended_production=recommended, channels=channel_results,
        allocation=allocation, explanation=explanation,
    )


def _previous_year(value: date) -> date:
    """Return the comparable date one year earlier, including leap-day safety."""
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _resolve_depot(order_type: str, fulfillment_location: str | None) -> str | None:
    normalized_type = " ".join(order_type.casefold().split())
    normalized_location = " ".join((fulfillment_location or "").casefold().split())
    if "zalando fulfilled" in normalized_type:
        return "Zalando Offerable"
    if "partner fulfilled" not in normalized_type:
        return None
    if "ekol" in normalized_location:
        return "Ekol"
    if "paris" in normalized_location:
        return "Paris Shop"
    return None


def _largest_remainder(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Allocate an integer total exactly, with deterministic largest remainders."""
    result = {key: 0 for key in weights}
    if total <= 0 or not weights:
        return result
    positive = {key: max(0.0, value) for key, value in weights.items()}
    denominator = sum(positive.values())
    if denominator <= 0:
        first = sorted(positive)[0]
        result[first] = total
        return result
    raw = {key: total * value / denominator for key, value in positive.items()}
    result = {key: int(value) for key, value in raw.items()}
    remaining = total - sum(result.values())
    order = sorted(raw, key=lambda key: (-(raw[key] - result[key]), key))
    for key in order[:remaining]:
        result[key] += 1
    return result


def _matches_filters(product, filters) -> bool:
    return (
        (not filters.product_types or product.product_type in filters.product_types)
        and (not filters.statuses or product.status in filters.statuses)
        and (not filters.brands or product.brand in filters.brands)
    )


def calculate_portfolio(request: PortfolioPlanningRequest) -> PortfolioPlanningResult:
    """Calculate the approved Vizyon method for an entire product/size portfolio.

    The calculation intentionally excludes statistical challengers, scenarios,
    future supply and legacy-SKU history. All production is allocated to the
    three approved depots using historical net demand and current depot stock.
    """
    reference_start = _previous_year(request.policy.target_start)
    reference_end = _previous_year(request.policy.target_end)
    filters = request.filters
    products = [product for product in request.products if _matches_filters(product, filters)]
    products_by_sku = {product.sku: product for product in products}
    selected_skus = set(products_by_sku)

    warnings: list[str] = []
    if reference_start < DATA_FLOOR:
        warnings.append(
            "Referans dönemi 01.09.2025 öncesine taşıyor. Eski ürün geçmişi kullanılmadığı için sonuç eksik olabilir."
        )

    gross_by_sku: dict[str, int] = defaultdict(int)
    returns_by_sku: dict[str, int] = defaultdict(int)
    net_by_sku_depot: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    invalid_sales_rows = 0
    for row in request.sales:
        if row.sku not in selected_skus:
            continue
        if not (reference_start <= row.transaction_date <= reference_end):
            continue
        depot = _resolve_depot(row.order_type, row.fulfillment_location)
        if depot is None:
            invalid_sales_rows += 1
            continue
        gross_by_sku[row.sku] += row.gross_sales
        returns_by_sku[row.sku] += row.returns
        net_by_sku_depot[row.sku][depot] += row.gross_sales - row.returns
    if invalid_sales_rows:
        warnings.append(
            f"{invalid_sales_rows} satış satırı Order Type/lokasyon eşleşmediği için hesap dışında bırakıldı."
        )

    stock_by_sku_depot: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in request.inventory:
        if row.sku in selected_skus:
            stock_by_sku_depot[row.sku][row.depot] += row.sellable_quantity

    lines: list[PortfolioLineResult] = []
    for product in products:
        sku = product.sku
        gross = gross_by_sku[sku]
        returned = returns_by_sku[sku]
        net_by_depot = {depot: max(0, net_by_sku_depot[sku][depot]) for depot in DEPOTS}
        net = sum(net_by_depot.values())
        forecast = ceil(net * request.policy.growth_factor)
        stock_by_depot = {depot: stock_by_sku_depot[sku][depot] for depot in DEPOTS}
        stock = sum(stock_by_depot.values())
        need = max(0, forecast - stock)

        demand_weights = {depot: float(net_by_depot[depot]) for depot in DEPOTS}
        target_by_depot = _largest_remainder(forecast, demand_weights)
        deficits = {
            depot: max(0, target_by_depot[depot] - stock_by_depot[depot])
            for depot in DEPOTS
        }
        allocation_weights = {depot: float(deficits[depot]) for depot in DEPOTS}
        allocated = _largest_remainder(need, allocation_weights)
        net_denominator = sum(net_by_depot.values())

        depot_rows = [
            DepotAllocation(
                depot=depot,
                historical_net_sales=net_by_depot[depot],
                demand_share=(net_by_depot[depot] / net_denominator if net_denominator else 0.0),
                target_demand=target_by_depot[depot],
                sellable_stock=stock_by_depot[depot],
                deficit=deficits[depot],
                allocated_production=allocated[depot],
            )
            for depot in DEPOTS
        ]

        coverage = stock / forecast if forecast else None
        if need == 0:
            priority_level, priority_label = 4, "Üretim gerekmiyor"
            reason = "Satılabilir stok hedef dönem tahminini karşılıyor."
        elif stock == 0:
            priority_level, priority_label = 1, "Kritik"
            reason = "Tahmin var, satılabilir stok sıfır."
        elif coverage is not None and coverage < 0.5:
            priority_level, priority_label = 2, "Yüksek"
            reason = f"Stok tahminin yalnızca %{coverage * 100:.1f}'ini karşılıyor."
        else:
            priority_level, priority_label = 3, "Planlı"
            reason = f"Tahmin sonrası {need} adet açık bulunuyor."

        explanation = [
            f"Eş dönem net satış: {gross} brüt − {returned} iade = {net} adet.",
            f"Satış tahmini: {net} × {request.policy.growth_factor:g} = {forecast} adet (yukarı yuvarlandı).",
            f"Üretim ihtiyacı: max(0, {forecast} − {stock} satılabilir stok) = {need} adet.",
            "Depo önerisi Order Type, fulfillment location, geçmiş net satış payı ve mevcut depo stok açığıyla hesaplandı.",
        ]
        lines.append(PortfolioLineResult(
            priority_level=priority_level,
            priority_label=priority_label,
            priority_reason=reason,
            sku=sku,
            product=product.product,
            size=product.size,
            color=product.color,
            brand=product.brand,
            product_type=product.product_type,
            status=product.status,
            gross_sales=gross,
            returns=returned,
            net_sales=net,
            growth_factor=request.policy.growth_factor,
            forecast_demand=forecast,
            sellable_stock=stock,
            stock_coverage=coverage,
            production_need=need,
            depot_allocations=depot_rows,
            explanation=explanation,
        ))

    lines.sort(key=lambda row: (
        row.priority_level,
        row.stock_coverage if row.stock_coverage is not None else 1_000_000,
        -row.production_need,
        -row.net_sales,
        row.sku,
    ))
    for rank, row in enumerate(lines, start=1):
        row.priority_rank = rank

    depot_totals = []
    for depot in DEPOTS:
        depot_totals.append(DepotTotal(
            depot=depot,
            historical_net_sales=sum(
                allocation.historical_net_sales
                for row in lines for allocation in row.depot_allocations
                if allocation.depot == depot
            ),
            sellable_stock=sum(
                allocation.sellable_stock
                for row in lines for allocation in row.depot_allocations
                if allocation.depot == depot
            ),
            target_demand=sum(
                allocation.target_demand
                for row in lines for allocation in row.depot_allocations
                if allocation.depot == depot
            ),
            allocated_production=sum(
                allocation.allocated_production
                for row in lines for allocation in row.depot_allocations
                if allocation.depot == depot
            ),
        ))

    summary = PortfolioSummary(
        product_size_count=len(lines),
        gross_sales=sum(row.gross_sales for row in lines),
        returns=sum(row.returns for row in lines),
        net_sales=sum(row.net_sales for row in lines),
        forecast_demand=sum(row.forecast_demand for row in lines),
        sellable_stock=sum(row.sellable_stock for row in lines),
        production_need=sum(row.production_need for row in lines),
        critical_size_count=sum(row.priority_level <= 2 for row in lines),
        depot_totals=depot_totals,
    )
    return PortfolioPlanningResult(
        plan_name=request.plan_name,
        method="Eş dönem net satış × büyüme çarpanı",
        target_start=request.policy.target_start,
        target_end=request.policy.target_end,
        reference_start=reference_start,
        reference_end=reference_end,
        growth_factor=request.policy.growth_factor,
        filters=filters,
        summary=summary,
        lines=lines,
        warnings=warnings,
    )
