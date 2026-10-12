from datetime import date
import json
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException

from .engine import calculate_plan, calculate_portfolio
from .governance import PlanRepository, authorize, quality_checks
from .netsuite import NetSuiteAdapter, NetSuiteClient, NetSuiteConfig
from .schemas import (
    DataQualityCheck,
    ManualOverrideRequest,
    NetSuitePlanRequest,
    PlanRun,
    PlanRunSummary,
    PlanningRequest,
    PlanningResult,
    PortfolioPlanningRequest,
    PortfolioPlanningResult,
    TransitionRequest,
)


app = FastAPI(
    title="Vizyon Deri Talep ve Üretim Planlama API",
    version="1.0.0",
    description="Eş dönem net satış, üretim ihtiyacı ve depo alokasyonu.",
)
repository = PlanRepository()


def _netsuite_diagnostic_checks(diagnostics: dict) -> list[DataQualityCheck]:
    rejected = diagnostics["rejected_counts"]
    return [
        DataQualityCheck(
            code="NETSUITE_REJECTED_ITEMS",
            status="warn" if rejected["items"] else "pass",
            title="NetSuite ürün dönüşümü",
            detail=f"{rejected['items']} eksik alanlı ürün plan dışında bırakıldı." if rejected["items"] else "Tüm ürün satırları kanonik modele dönüştürüldü.",
        ),
        DataQualityCheck(
            code="NETSUITE_REJECTED_SALES",
            status="fail" if rejected["sales_returns"] else "pass",
            title="NetSuite satış/iade dönüşümü",
            detail=f"{rejected['sales_returns']} satış/iade satırı SKU veya Order Type eksikliği nedeniyle alınamadı." if rejected["sales_returns"] else "Tüm satış/iade satırları kanonik modele dönüştürüldü.",
            blocking=bool(rejected["sales_returns"]),
        ),
        DataQualityCheck(
            code="NETSUITE_UNMAPPED_LOCATIONS",
            status="warn" if rejected["inventory"] else "pass",
            title="NetSuite depo eşlemesi",
            detail=f"{rejected['inventory']} stok satırı üç hedef depo dışında kaldı." if rejected["inventory"] else "Tüm stok satırları hedef depolarla eşleşti.",
        ),
    ]


@app.get("/health")
def health() -> dict:
    return repository.health()


@app.get("/api/v1/meta")
def metadata() -> dict:
    return {
        "product_types": ["Kürk", "Deri"],
        "statuses": ["S1"],
        "brands": ["VSP"],
        "depots": ["Zalando Offerable", "Ekol", "Paris Shop"],
        "data_floor": "2025-09-01",
        "method": "Eş dönem net satış × büyüme çarpanı",
        "source": "demo",
        "governance": {
            "versioning": True,
            "quality_gates": True,
            "manual_override_audit": True,
            "approval_flow": ["draft", "review", "approved", "published"],
        },
    }


@app.post("/api/v1/plans/calculate", response_model=PlanningResult)
def calculate(request: PlanningRequest) -> PlanningResult:
    """Legacy single-SKU endpoint, kept for backward compatibility."""
    return calculate_plan(request)


@app.post("/api/v1/plans/portfolio", response_model=PortfolioPlanningResult)
def calculate_portfolio_endpoint(request: PortfolioPlanningRequest) -> PortfolioPlanningResult:
    return calculate_portfolio(request)


DEMO_VARIANTS = [
    ("KK61450-BROWN-34", "Lyza Shearling Coat", "34", "Clotho Brown", "Kürk", 200, 90, 50, 5),
    ("KK61450-BROWN-36", "Lyza Shearling Coat", "36", "Clotho Brown", "Kürk", 300, 180, 80, 12),
    ("KK61450-BROWN-38", "Lyza Shearling Coat", "38", "Clotho Brown", "Kürk", 400, 210, 100, 18),
    ("KK61450-BROWN-40", "Lyza Shearling Coat", "40", "Clotho Brown", "Kürk", 300, 140, 70, 10),
    ("DD40319-BLACK-36", "Serra Leather Jacket", "36", "Black", "Deri", 200, 130, 70, 10),
    ("DD40319-BLACK-38", "Serra Leather Jacket", "38", "Black", "Deri", 200, 110, 60, 8),
    ("DD46476-BLACK-48", "Marco Leather Jacket", "48", "Black", "Deri", 200, 80, 50, 7),
    ("DD46476-BLACK-50", "Marco Leather Jacket", "50", "Black", "Deri", 200, 120, 80, 10),
]


def _demo_request(payload: dict) -> PortfolioPlanningRequest:
    target_start = date.fromisoformat(payload.get("target_start", "2026-11-01"))
    target_end = date.fromisoformat(payload.get("target_end", "2027-02-28"))
    growth_factor = float(payload.get("growth_factor", 1.8))
    products = []
    sales = []
    inventory = []
    for sku, product, size, color, product_type, net, zalando_stock, ekol_stock, paris_stock in DEMO_VARIANTS:
        products.append({
            "sku": sku,
            "product": product,
            "size": size,
            "color": color,
            "brand": "VSP",
            "product_type": product_type,
            "status": "S1",
        })
        depot_nets = {
            "Zalando Offerable": round(net * 0.60),
            "Ekol": round(net * 0.35),
        }
        depot_nets["Paris Shop"] = net - sum(depot_nets.values())
        for depot, depot_net in depot_nets.items():
            returned = max(1, round(depot_net * 0.18))
            order_type = "Zalando Fulfilled" if depot == "Zalando Offerable" else "Partner Fulfilled"
            location = None if depot == "Zalando Offerable" else depot
            sales.append({
                "sku": sku,
                "transaction_date": "2025-12-15",
                "gross_sales": depot_net + returned,
                "returns": returned,
                "order_type": order_type,
                "fulfillment_location": location,
            })
        for depot, quantity in (
            ("Zalando Offerable", zalando_stock),
            ("Ekol", ekol_stock),
            ("Paris Shop", paris_stock),
        ):
            inventory.append({"sku": sku, "depot": depot, "sellable_quantity": quantity})

    return PortfolioPlanningRequest.model_validate({
        "plan_name": payload.get("plan_name", "2026/27 Kış üretim planı"),
        "filters": {
            "product_types": payload.get("product_types", ["Kürk", "Deri"]),
            "statuses": payload.get("statuses", ["S1"]),
            "brands": payload.get("brands", ["VSP"]),
        },
        "policy": {
            "target_start": target_start,
            "target_end": target_end,
            "growth_factor": growth_factor,
        },
        "products": products,
        "sales": sales,
        "inventory": inventory,
    })


@app.post("/api/v1/plans/demo", response_model=PortfolioPlanningResult)
def demo_plan(payload: dict) -> PortfolioPlanningResult:
    return calculate_portfolio(_demo_request(payload))


def _identity(x_user: str | None, x_role: str | None) -> tuple[str, str]:
    return unquote(x_user or "Demo%20Kullan%C4%B1c%C4%B1").strip(), (x_role or "planner").strip().casefold()


def _require(role: str, action: str) -> None:
    if not authorize(role, action):
        raise HTTPException(status_code=403, detail=f"'{role}' rolü '{action}' işlemini yapamaz.")


@app.post("/api/v1/managed-plans/demo", response_model=PlanRun)
def create_managed_demo(
    payload: dict,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> PlanRun:
    actor, role = _identity(x_user, x_role)
    _require(role, "create")
    request = _demo_request(payload)
    result = calculate_portfolio(request)
    return repository.create(request, result, quality_checks(request, result), actor, role, "demo_snapshot")


@app.post("/api/v1/managed-plans/portfolio", response_model=PlanRun)
def create_managed_portfolio(
    request: PortfolioPlanningRequest,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> PlanRun:
    actor, role = _identity(x_user, x_role)
    _require(role, "create")
    result = calculate_portfolio(request)
    return repository.create(request, result, quality_checks(request, result), actor, role, "netsuite_payload")


@app.get("/api/v1/managed-plans", response_model=list[PlanRunSummary])
def list_managed_plans(limit: int = 30) -> list[PlanRunSummary]:
    return repository.list(max(1, min(limit, 100)))


@app.get("/api/v1/managed-plans/{plan_id}", response_model=PlanRun)
def get_managed_plan(plan_id: str) -> PlanRun:
    try:
        return repository.get(plan_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan bulunamadı.") from None


@app.post("/api/v1/managed-plans/{plan_id}/override", response_model=PlanRun)
def override_managed_plan(
    plan_id: str,
    change: ManualOverrideRequest,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> PlanRun:
    actor, role = _identity(x_user, x_role)
    _require(role, "override")
    try:
        return repository.override(plan_id, change, actor, role)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan veya SKU bulunamadı.") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


def _netsuite_adapter() -> NetSuiteAdapter:
    try:
        config = NetSuiteConfig.from_env()
        return NetSuiteAdapter(NetSuiteClient(config))
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.get("/api/v1/integrations/netsuite/status")
def netsuite_status() -> dict:
    try:
        return NetSuiteConfig.from_env().public_status()
    except (ValueError, json.JSONDecodeError) as error:
        return {"configured": False, "read_only": True, "configuration_error": str(error)}


@app.post("/api/v1/integrations/netsuite/connection-test")
def netsuite_connection_test(
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict:
    _, role = _identity(x_user, x_role)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Bağlantı testini yalnızca yönetici çalıştırabilir.")
    try:
        return _netsuite_adapter().client.connection_test()
    except (RuntimeError, ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail=f"NetSuite verisi işlenemedi: {error}") from None


@app.post("/api/v1/integrations/netsuite/preview")
def netsuite_preview(
    plan: NetSuitePlanRequest,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> dict:
    _, role = _identity(x_user, x_role)
    _require(role, "create")
    try:
        request, diagnostics = _netsuite_adapter().fetch(plan)
        result = calculate_portfolio(request)
        checks = quality_checks(request, result) + _netsuite_diagnostic_checks(diagnostics)
        return {
            "diagnostics": diagnostics,
            "summary": result.summary.model_dump(mode="json"),
            "quality_checks": [item.model_dump(mode="json") for item in checks],
            "can_create_plan": not any(item.blocking and item.status == "fail" for item in checks),
        }
    except (RuntimeError, ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail=f"NetSuite verisi işlenemedi: {error}") from None


@app.post("/api/v1/integrations/netsuite/sync-plan", response_model=PlanRun)
def netsuite_sync_plan(
    plan: NetSuitePlanRequest,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> PlanRun:
    actor, role = _identity(x_user, x_role)
    _require(role, "create")
    try:
        request, diagnostics = _netsuite_adapter().fetch(plan)
        result = calculate_portfolio(request)
        checks = quality_checks(request, result) + _netsuite_diagnostic_checks(diagnostics)
        return repository.create(request, result, checks, actor, role, "netsuite_suiteql")
    except (RuntimeError, ValueError, KeyError) as error:
        raise HTTPException(status_code=502, detail=f"NetSuite verisi işlenemedi: {error}") from None


@app.post("/api/v1/managed-plans/{plan_id}/transition", response_model=PlanRun)
def transition_managed_plan(
    plan_id: str,
    transition: TransitionRequest,
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> PlanRun:
    actor, role = _identity(x_user, x_role)
    _require(role, transition.action)
    try:
        return repository.transition(plan_id, transition.action, transition.note, actor, role)
    except KeyError:
        raise HTTPException(status_code=404, detail="Plan bulunamadı.") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
