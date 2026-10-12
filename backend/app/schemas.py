from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SalesPerformance(BaseModel):
    channel: str
    gross_sales: int = Field(ge=0)
    returns: int = Field(ge=0)

    @model_validator(mode="after")
    def returns_cannot_exceed_sales(self):
        if self.returns > self.gross_sales:
            raise ValueError("returns cannot exceed gross_sales")
        return self


class InventoryPosition(BaseModel):
    location: str
    offerable: int = Field(ge=0)
    committed: int = Field(default=0, ge=0)
    blocked: int = Field(default=0, ge=0)


class FutureSupply(BaseModel):
    location: str
    quantity: int = Field(ge=0)
    expected_date: date
    source: Literal["inbound", "production", "transfer"]


class AccessRule(BaseModel):
    channel: str
    location: str
    can_access: bool = True
    priority: int = Field(default=100, ge=1)
    minimum_stock: int = Field(default=0, ge=0)


class PlanningPolicy(BaseModel):
    reference_start: date
    reference_end: date
    plan_start: date
    plan_end: date
    growth_factor: float = Field(default=1.0, gt=0)
    safety_stock: int = Field(default=0, ge=0)
    production_multiple: int = Field(default=1, ge=1)
    minimum_production_quantity: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_periods(self):
        if self.reference_end < self.reference_start:
            raise ValueError("reference_end must be on or after reference_start")
        if self.plan_end < self.plan_start:
            raise ValueError("plan_end must be on or after plan_start")
        return self


class PlanningRequest(BaseModel):
    scenario_name: str = "Demand plan"
    sku: str
    product: str
    size: str
    brand: str | None = None
    product_type: str | None = None
    status: str | None = None
    policy: PlanningPolicy
    sales: list[SalesPerformance]
    inventory: list[InventoryPosition] = []
    future_supply: list[FutureSupply] = []
    access_rules: list[AccessRule]


class ChannelResult(BaseModel):
    channel: str
    gross_sales: int
    returns: int
    return_rate: float
    net_sales: int
    base_demand: float
    forecast_demand: int
    accessible_current_stock: int
    accessible_future_supply: int
    allocated_existing_supply: int
    unmet_demand: int


class AllocationResult(BaseModel):
    channel: str
    location: str
    quantity: int
    reason: str


class PlanningResult(BaseModel):
    scenario_name: str
    sku: str
    product: str
    size: str
    reference_days: int
    planning_days: int
    gross_sales: int
    returns: int
    return_rate: float
    net_sales: int
    growth_factor: float
    forecast_demand: int
    safety_stock: int
    usable_current_stock: int
    usable_future_supply: int
    calculated_need: int
    recommended_production: int
    channels: list[ChannelResult]
    allocation: list[AllocationResult]
    explanation: list[str]


# Revize temel ürün: eş dönem net satış × büyüme, ürün/beden ihtiyacı
# ve üç depoya deterministik alokasyon.
DepotName = Literal["Zalando Offerable", "Ekol", "Paris Shop"]


class ProductVariant(BaseModel):
    sku: str
    product: str
    size: str
    color: str | None = None
    brand: str
    product_type: str
    status: str


class HistoricalSalesLine(BaseModel):
    sku: str
    transaction_date: date
    gross_sales: int = Field(ge=0)
    returns: int = Field(default=0, ge=0)
    order_type: str
    fulfillment_location: str | None = None

    # İade, NetSuite'te satıştan ayrı bir Credit Memo satırı olarak gelebilir.
    # Bu yüzden satır seviyesinde returns <= gross_sales koşulu aranmaz.


class SellableInventoryLine(BaseModel):
    sku: str
    depot: DepotName
    sellable_quantity: int = Field(ge=0)


class PortfolioFilters(BaseModel):
    product_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)


class PortfolioPolicy(BaseModel):
    target_start: date
    target_end: date
    growth_factor: float = Field(default=1.0, gt=0, le=10)

    @model_validator(mode="after")
    def validate_target_period(self):
        if self.target_end < self.target_start:
            raise ValueError("target_end must be on or after target_start")
        return self


class PortfolioPlanningRequest(BaseModel):
    plan_name: str = "Temel üretim planı"
    filters: PortfolioFilters
    policy: PortfolioPolicy
    products: list[ProductVariant]
    sales: list[HistoricalSalesLine]
    inventory: list[SellableInventoryLine]


class NetSuitePlanRequest(BaseModel):
    plan_name: str = "NetSuite üretim planı"
    filters: PortfolioFilters
    policy: PortfolioPolicy


class DepotAllocation(BaseModel):
    depot: DepotName
    historical_net_sales: int
    demand_share: float
    target_demand: int
    sellable_stock: int
    deficit: int
    allocated_production: int


class PortfolioLineResult(BaseModel):
    priority_rank: int = 0
    priority_level: int
    priority_label: str
    priority_reason: str
    sku: str
    product: str
    size: str
    color: str | None = None
    brand: str
    product_type: str
    status: str
    gross_sales: int
    returns: int
    net_sales: int
    growth_factor: float
    forecast_demand: int
    sellable_stock: int
    stock_coverage: float | None
    production_need: int
    depot_allocations: list[DepotAllocation]
    explanation: list[str]


class DepotTotal(BaseModel):
    depot: DepotName
    historical_net_sales: int
    sellable_stock: int
    target_demand: int
    allocated_production: int


class PortfolioSummary(BaseModel):
    product_size_count: int
    gross_sales: int
    returns: int
    net_sales: int
    forecast_demand: int
    sellable_stock: int
    production_need: int
    critical_size_count: int
    depot_totals: list[DepotTotal]


class PortfolioPlanningResult(BaseModel):
    plan_name: str
    method: str
    target_start: date
    target_end: date
    reference_start: date
    reference_end: date
    growth_factor: float
    filters: PortfolioFilters
    summary: PortfolioSummary
    lines: list[PortfolioLineResult]
    warnings: list[str]


# Operasyonel güvenilirlik ve onay akışı modelleri.
PlanStatus = Literal["draft", "review", "approved", "published"]


class DataQualityCheck(BaseModel):
    code: str
    status: Literal["pass", "warn", "fail"]
    title: str
    detail: str
    blocking: bool = False


class AuditEvent(BaseModel):
    timestamp: datetime
    actor: str
    role: str
    action: str
    detail: str


class PlanRun(BaseModel):
    id: str
    version: int
    plan_name: str
    status: PlanStatus
    created_at: datetime
    updated_at: datetime
    created_by: str
    source: str
    source_timestamp: datetime
    request: PortfolioPlanningRequest
    result: PortfolioPlanningResult
    quality_checks: list[DataQualityCheck]
    audit: list[AuditEvent]


class PlanRunSummary(BaseModel):
    id: str
    version: int
    plan_name: str
    status: PlanStatus
    created_at: datetime
    updated_at: datetime
    created_by: str
    production_need: int
    blocking_check_count: int
    warning_count: int


class ManualOverrideRequest(BaseModel):
    sku: str
    production_need: int = Field(ge=0)
    reason: str = Field(min_length=8, max_length=500)
    depot_allocations: dict[DepotName, int] | None = None


class TransitionRequest(BaseModel):
    action: Literal["submit", "approve", "reject", "publish"]
    note: str = Field(default="", max_length=500)
