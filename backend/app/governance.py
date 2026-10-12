"""Persistent plan governance: quality gates, versioning and audit trail."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .schemas import (
    AuditEvent,
    DataQualityCheck,
    DepotAllocation,
    DepotTotal,
    ManualOverrideRequest,
    PlanRun,
    PlanRunSummary,
    PlanStatus,
    PortfolioPlanningRequest,
    PortfolioPlanningResult,
)


ROLE_ACTIONS = {
    "viewer": {"read"},
    "planner": {"read", "create", "override", "submit"},
    "approver": {"read", "approve", "reject"},
    "publisher": {"read", "publish"},
    "admin": {"read", "create", "override", "submit", "approve", "reject", "publish"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def authorize(role: str, action: str) -> bool:
    return action in ROLE_ACTIONS.get(role.casefold(), set())


def quality_checks(request: PortfolioPlanningRequest, result: PortfolioPlanningResult) -> list[DataQualityCheck]:
    product_skus = [item.sku for item in request.products]
    known = set(product_skus)
    included = {line.sku for line in result.lines}
    duplicate_count = len(product_skus) - len(known)
    unknown_sales = {row.sku for row in request.sales if row.sku not in known}
    unknown_stock = {row.sku for row in request.inventory if row.sku not in known}
    reference_sales = [
        row for row in request.sales
        if result.reference_start <= row.transaction_date <= result.reference_end and row.sku in included
    ]
    expected_gross = sum(row.gross_sales for row in reference_sales)
    expected_returns = sum(row.returns for row in reference_sales)
    allocation_total = sum(row.allocated_production for row in result.summary.depot_totals)
    line_allocation_total = sum(
        item.allocated_production for line in result.lines for item in line.depot_allocations
    )
    checks = [
        DataQualityCheck(
            code="UNIQUE_SKU", status="fail" if duplicate_count else "pass",
            title="Ürün ana verisi tekilliği",
            detail=(f"{duplicate_count} mükerrer SKU bulundu." if duplicate_count else f"{len(known)} SKU tekil."),
            blocking=bool(duplicate_count),
        ),
        DataQualityCheck(
            code="REFERENCE_DATA", status="pass" if reference_sales else "fail",
            title="Eş dönem satış verisi",
            detail=(f"{len(reference_sales)} satış/iade satırı eş döneme dahil edildi." if reference_sales else "Eş dönem için satış/iade satırı bulunamadı."),
            blocking=not bool(reference_sales),
        ),
        DataQualityCheck(
            code="UNKNOWN_SALES_SKU", status="warn" if unknown_sales else "pass",
            title="Satış–ürün eşleşmesi",
            detail=(f"Ürün kartı olmayan {len(unknown_sales)} satış SKU'su dışlandı." if unknown_sales else "Tüm satış SKU'ları ürün kartıyla eşleşiyor."),
        ),
        DataQualityCheck(
            code="UNKNOWN_STOCK_SKU", status="warn" if unknown_stock else "pass",
            title="Stok–ürün eşleşmesi",
            detail=(f"Ürün kartı olmayan {len(unknown_stock)} stok SKU'su dışlandı." if unknown_stock else "Tüm stok SKU'ları ürün kartıyla eşleşiyor."),
        ),
        DataQualityCheck(
            code="NET_SALES_RECONCILIATION",
            status="pass" if expected_gross == result.summary.gross_sales and expected_returns == result.summary.returns else "fail",
            title="NetSuite satış mutabakatı",
            detail=f"Kaynak: {expected_gross} brüt − {expected_returns} iade. Plan: {result.summary.net_sales} net.",
            blocking=expected_gross != result.summary.gross_sales or expected_returns != result.summary.returns,
        ),
        DataQualityCheck(
            code="ALLOCATION_RECONCILIATION",
            status="pass" if allocation_total == result.summary.production_need == line_allocation_total else "fail",
            title="Üretim–depo mutabakatı",
            detail=f"Üretim {result.summary.production_need}; depo toplamı {allocation_total}; satır toplamı {line_allocation_total}.",
            blocking=allocation_total != result.summary.production_need or line_allocation_total != result.summary.production_need,
        ),
        DataQualityCheck(
            code="DATA_FRESHNESS", status="pass",
            title="Veri anı",
            detail=f"Plan girdisi {utc_now().strftime('%d.%m.%Y %H:%M UTC')} tarihinde donduruldu.",
        ),
    ]
    for warning in result.warnings:
        checks.append(DataQualityCheck(
            code="ENGINE_WARNING", status="warn", title="Hesap motoru uyarısı", detail=warning,
        ))
    return checks


def recalculate_summary(result: PortfolioPlanningResult) -> PortfolioPlanningResult:
    depot_names = ("Zalando Offerable", "Ekol", "Paris Shop")
    depot_totals = []
    for depot in depot_names:
        allocations = [
            allocation for line in result.lines for allocation in line.depot_allocations
            if allocation.depot == depot
        ]
        depot_totals.append(DepotTotal(
            depot=depot,
            historical_net_sales=sum(item.historical_net_sales for item in allocations),
            sellable_stock=sum(item.sellable_stock for item in allocations),
            target_demand=sum(item.target_demand for item in allocations),
            allocated_production=sum(item.allocated_production for item in allocations),
        ))
    result.summary.production_need = sum(line.production_need for line in result.lines)
    result.summary.depot_totals = depot_totals
    return result


class PlanRepository:
    def __init__(self, path: str | None = None):
        default = Path(__file__).resolve().parents[2] / "data" / "planning.db"
        self.path = Path(path or os.getenv("PLANNING_DB_PATH", str(default)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS plan_runs (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    plan_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_timestamp TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    checks_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plan_runs(id)
                );
                CREATE INDEX IF NOT EXISTS ix_plan_runs_created ON plan_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_audit_plan ON audit_events(plan_id, id);
            """)

    def _backup(self) -> None:
        """Create a recoverable SQLite snapshot and retain the latest 20 copies."""
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"planning-{utc_now().strftime('%Y%m%dT%H%M%S%f')}.db"
        source = self._connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        backups = sorted(backup_dir.glob("planning-*.db"), reverse=True)
        for expired in backups[20:]:
            expired.unlink(missing_ok=True)

    def _next_version(self, connection: sqlite3.Connection, name: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS value FROM plan_runs WHERE plan_name = ?", (name,)
        ).fetchone()
        return int(row["value"]) + 1

    def create(self, request: PortfolioPlanningRequest, result: PortfolioPlanningResult,
               checks: list[DataQualityCheck], actor: str, role: str, source: str) -> PlanRun:
        now = utc_now()
        plan_id = f"PLN-{now.strftime('%Y%m%d')}-{uuid4().hex[:6].upper()}"
        with self._lock, self._connect() as connection:
            version = self._next_version(connection, request.plan_name)
            connection.execute(
                """INSERT INTO plan_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (plan_id, version, request.plan_name, "draft", now.isoformat(), now.isoformat(),
                 actor, source, now.isoformat(), request.model_dump_json(), result.model_dump_json(),
                 json.dumps([item.model_dump(mode="json") for item in checks], ensure_ascii=False)),
            )
            self._audit(connection, plan_id, actor, role, "created", f"v{version} hesaplandı ve veri kontrolleri çalıştırıldı.")
        self._backup()
        return self.get(plan_id)

    def _audit(self, connection: sqlite3.Connection, plan_id: str, actor: str,
               role: str, action: str, detail: str) -> None:
        connection.execute(
            "INSERT INTO audit_events(plan_id,timestamp,actor,role,action,detail) VALUES(?,?,?,?,?,?)",
            (plan_id, utc_now().isoformat(), actor, role, action, detail),
        )

    def get(self, plan_id: str) -> PlanRun:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM plan_runs WHERE id = ?", (plan_id,)).fetchone()
            if not row:
                raise KeyError(plan_id)
            events = connection.execute(
                "SELECT timestamp,actor,role,action,detail FROM audit_events WHERE plan_id = ? ORDER BY id", (plan_id,)
            ).fetchall()
        return PlanRun(
            id=row["id"], version=row["version"], plan_name=row["plan_name"], status=row["status"],
            created_at=row["created_at"], updated_at=row["updated_at"], created_by=row["created_by"],
            source=row["source"], source_timestamp=row["source_timestamp"],
            request=PortfolioPlanningRequest.model_validate_json(row["request_json"]),
            result=PortfolioPlanningResult.model_validate_json(row["result_json"]),
            quality_checks=[DataQualityCheck.model_validate(item) for item in json.loads(row["checks_json"])],
            audit=[AuditEvent.model_validate(dict(item)) for item in events],
        )

    def list(self, limit: int = 30) -> list[PlanRunSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,version,plan_name,status,created_at,updated_at,created_by,result_json,checks_json "
                "FROM plan_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        summaries = []
        for row in rows:
            result = PortfolioPlanningResult.model_validate_json(row["result_json"])
            checks = [DataQualityCheck.model_validate(item) for item in json.loads(row["checks_json"])]
            summaries.append(PlanRunSummary(
                id=row["id"], version=row["version"], plan_name=row["plan_name"], status=row["status"],
                created_at=row["created_at"], updated_at=row["updated_at"], created_by=row["created_by"],
                production_need=result.summary.production_need,
                blocking_check_count=sum(1 for item in checks if item.blocking and item.status == "fail"),
                warning_count=sum(1 for item in checks if item.status == "warn"),
            ))
        return summaries

    def override(self, plan_id: str, change: ManualOverrideRequest, actor: str, role: str) -> PlanRun:
        plan = self.get(plan_id)
        if plan.status != "draft":
            raise ValueError("Yalnızca taslak planlarda manuel düzeltme yapılabilir.")
        line = next((item for item in plan.result.lines if item.sku == change.sku), None)
        if not line:
            raise KeyError(change.sku)
        old = line.production_need
        if change.depot_allocations is not None:
            if sum(change.depot_allocations.values()) != change.production_need:
                raise ValueError("Depo dağılımı toplamı yeni üretim ihtiyacına eşit olmalıdır.")
            for allocation in line.depot_allocations:
                allocation.allocated_production = change.depot_allocations.get(allocation.depot, 0)
        else:
            current = {item.depot: item.allocated_production for item in line.depot_allocations}
            total = sum(current.values())
            remaining = change.production_need
            for index, allocation in enumerate(line.depot_allocations):
                value = remaining if index == len(line.depot_allocations) - 1 else round(change.production_need * current[allocation.depot] / total) if total else 0
                value = min(remaining, value)
                allocation.allocated_production = value
                remaining -= value
        line.production_need = change.production_need
        line.explanation.append(f"Manuel revizyon: {old} → {change.production_need}. Gerekçe: {change.reason}")
        recalculate_summary(plan.result)
        plan.quality_checks = quality_checks(plan.request, plan.result)
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE plan_runs SET result_json=?, checks_json=?, updated_at=? WHERE id=?",
                (plan.result.model_dump_json(),
                 json.dumps([item.model_dump(mode="json") for item in plan.quality_checks], ensure_ascii=False),
                 now.isoformat(), plan_id),
            )
            self._audit(connection, plan_id, actor, role, "override",
                        f"{change.sku}: {old} → {change.production_need}. Gerekçe: {change.reason}")
        self._backup()
        return self.get(plan_id)

    def transition(self, plan_id: str, action: str, note: str, actor: str, role: str) -> PlanRun:
        plan = self.get(plan_id)
        transitions: dict[tuple[PlanStatus, str], PlanStatus] = {
            ("draft", "submit"): "review",
            ("review", "approve"): "approved",
            ("review", "reject"): "draft",
            ("approved", "publish"): "published",
        }
        target = transitions.get((plan.status, action))
        if not target:
            raise ValueError(f"{plan.status} durumunda '{action}' işlemi yapılamaz.")
        blockers = [item for item in plan.quality_checks if item.blocking and item.status == "fail"]
        if action in {"submit", "approve", "publish"} and blockers:
            raise ValueError("Bloklayıcı veri kalite hataları giderilmeden plan ilerletilemez.")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE plan_runs SET status=?, updated_at=? WHERE id=?", (target, now.isoformat(), plan_id))
            self._audit(connection, plan_id, actor, role, action, note or f"Durum {target} olarak güncellendi.")
        self._backup()
        return self.get(plan_id)

    def health(self) -> dict[str, str | int]:
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS value FROM plan_runs").fetchone()["value"]
        backups = sorted((self.path.parent / "backups").glob("planning-*.db"), reverse=True)
        return {
            "status": "ok", "storage": "sqlite", "plan_count": int(count),
            "backup_count": len(backups), "latest_backup": backups[0].name if backups else "none",
        }
