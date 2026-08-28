"""Real node implementations — the wired pipeline (issue #25).

Each node: (state, NodeContext) -> partial state update, one transaction per
node, one ledger entry per node transition (ARCHITECTURE §3.3). Determinism
discipline: the deterministic stages (validate/match/policy/gate) call the
pure engines from ``tools/``; only extract (agent) and near-dup (embedding)
touch models. The clock is injected via the context — no wall reads.

ExceptionTriage is a deliberate placeholder here: it opens the exception
record with the taxonomy-mapped findings so the human queue exists from day
one; the triage AGENT (evidence package + recommendation draft) lands with
#30. HumanReview is likewise a pass-through until the HITL service (#29/#31).
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from invoiceops_agent.agents.extraction import InvoiceExtraction
from invoiceops_agent.agents.triage import build_evidence_package
from invoiceops_agent.db.models import (
    ExceptionRecord,
    GoodsReceipt,
    Invoice,
    PurchaseOrder,
    Run,
    Vendor,
)
from invoiceops_agent.graph.runtime import NodeContext
from invoiceops_agent.graph.state import GraphState, Route
from invoiceops_agent.ledger.api import ActorType, LedgerAppend, writer
from invoiceops_agent.ledger.model_calls import ModelCallRef, model_call_context
from invoiceops_agent.tools import (
    exception_taxonomy,
    matching,
    policy,
    validation,
    validation_config,
)
from invoiceops_agent.tools.confidence_gate import GateInputs, decide
from invoiceops_agent.tools.near_dup import salient_text
from invoiceops_agent.versions import CURRENT, VersionPins

logger = logging.getLogger(__name__)

NodeResult = dict[str, Any]


class PipelineNodes:
    """Bound node callables; the builder wires these into the graph."""

    def __init__(self, ctx: NodeContext) -> None:
        self._ctx = ctx

    # ------------------------------------------------------------------ ingest

    async def ingest(self, state: GraphState) -> NodeResult:
        """Load the invoice + queued run; mark RUNNING; open the audit trail."""
        invoice_id = state.invoice_id
        if invoice_id is None:
            raise ValueError("pipeline ingest requires state.invoice_id")
        async with self._ctx.sessions() as session:
            invoice = await session.get(Invoice, invoice_id)
            if invoice is None:
                raise ValueError(f"invoice {invoice_id} not found")
            run = (
                await session.execute(
                    select(Run)
                    .where(Run.invoice_id == invoice_id, Run.status == "QUEUED")
                    .order_by(Run.run_id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run is None:  # resume path: the run is already RUNNING
                run = (
                    await session.execute(
                        select(Run)
                        .where(Run.invoice_id == invoice_id, Run.status == "RUNNING")
                        .order_by(Run.run_id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"no queued/running run for invoice {invoice_id}")

            run.status = "RUNNING"
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.SYSTEM,
                    actor_id="ingest",
                    run_id=run.run_id,
                    invoice_id=invoice.invoice_id,
                    event={
                        "event": "run.started",
                        "doc_ref": invoice.doc_ref,
                        "content_hash": invoice.content_hash,
                    },
                ),
            )
            await session.commit()
            return {
                "invoice_id": invoice.invoice_id,
                "run_db_id": run.run_id,
                "doc_ref": invoice.doc_ref,
                "content_hash": invoice.content_hash,
                "duplicate": False,  # exact dupes are rejected before the graph (#13)
                "node_trace": [*state.node_trace, "ingest"],
            }

    # ----------------------------------------------------------------- extract

    async def extract(self, state: GraphState) -> NodeResult:
        assert state.invoice_id is not None and state.run_db_id is not None
        token = model_call_context.set(
            ModelCallRef(run_id=state.run_db_id, invoice_id=state.invoice_id, stage="extract")
        )
        try:
            extraction = await self._ctx.extraction_agent.extract(
                state.doc_ref or "",
                None,  # sniff content type from the stored bytes
                run_id=state.run_db_id,
                invoice_id=state.invoice_id,
                scenario=self._ctx.gateway_scenario,
            )
        finally:
            model_call_context.reset(token)
        return {
            "extraction": extraction.model_dump(mode="json"),
            "node_trace": [*state.node_trace, "extract"],
        }

    # ---------------------------------------------------------------- validate

    async def validate(self, state: GraphState) -> NodeResult:
        extraction = _extraction(state)
        report = validation.validate_extraction(extraction)
        await self._ledger(
            state,
            ActorType.SYSTEM,
            "validate",
            {
                "event": "validate.completed",
                "passed": report.passed,
                "checks": report.as_dicts(),
                "validation_version": validation_config.VERSION,
            },
        )
        return {
            "validation": report.as_dicts(),
            "node_trace": [*state.node_trace, "validate"],
        }

    # --------------------------------------------------------------- match3way

    async def match3way(self, state: GraphState) -> NodeResult:
        extraction = _extraction(state)
        async with self._ctx.sessions() as session:
            po, gr, vendor = await _load_po_triple(session, extraction.po_number)
            po_for_match = (
                matching.po_from_erp(
                    po_number=po.po_number,
                    vendor_name=vendor.name if vendor else None,
                    currency=po.currency,
                    status=po.status,
                    ordered_at=po.ordered_at,
                    lines_jsonb=list(po.lines),
                )
                if po
                else None
            )
            gr_for_match = (
                matching.gr_from_erp(
                    gr_number=gr.gr_number,
                    po_number=po.po_number,  # GR references the PO by id, not number
                    received_jsonb=list(gr.received_qty),
                )
                if gr and po
                else None
            )
            # Backfill the denormalized read model the queue filters/sorts on
            # (#28). Extraction fields always; ERP refs when a PO was found.
            invoice = await session.get(Invoice, state.invoice_id) if state.invoice_id else None
            if invoice is not None:
                invoice.invoice_number = extraction.invoice_number
                invoice.currency = extraction.currency
                invoice.amount_total = extraction.total_amount
                invoice.issue_date = _parse_date(extraction.issue_date)
                if po is not None:
                    invoice.po_id = po.po_id
                    invoice.vendor_id = po.vendor_id
                await session.commit()
        result = matching.match3way(_invoice_for_match(extraction), po_for_match, gr_for_match)
        await self._ledger(
            state,
            ActorType.SYSTEM,
            "match3way",
            {"event": "match.completed", **result.as_dict()},
        )
        return {"match": result.as_dict(), "node_trace": [*state.node_trace, "match3way"]}

    # ------------------------------------------------------------------ policy

    async def policy(self, state: GraphState) -> NodeResult:
        extraction = _extraction(state)
        token = model_call_context.set(
            ModelCallRef(run_id=state.run_db_id, invoice_id=state.invoice_id, stage="policy")
        )
        try:
            async with self._ctx.sessions() as session:
                po, _gr, vendor = await _load_po_triple(session, extraction.po_number)
                near_dup_outcome = await self._ctx.near_dup.check_and_store(
                    session,
                    state.invoice_id or 0,
                    salient_text(extraction.model_dump(mode="json")),
                )
                report = policy.evaluate(
                    policy.PolicyContext(
                        invoice=policy.InvoiceFacts(
                            invoice_number=extraction.invoice_number,
                            vendor_name=extraction.vendor_name,
                            po_number=extraction.po_number,
                            currency=extraction.currency,
                            total_amount=extraction.total_amount,
                            iban=extraction.iban,
                            issue_date=_parse_date(extraction.issue_date),
                        ),
                        po=(
                            policy.PoFacts(
                                po_number=po.po_number, status=po.status, ordered_at=po.ordered_at
                            )
                            if po
                            else None
                        ),
                        vendor=(
                            policy.VendorFacts(
                                name=vendor.name,
                                iban=(vendor.bank_details or {}).get("iban"),
                                is_active=vendor.is_active,
                                risk_flags=tuple(vendor.risk_flags or ()),
                            )
                            if vendor
                            else None
                        ),
                        near_dup_hits=tuple(near_dup_outcome.hits),
                    )
                )
                await session.commit()
        finally:
            model_call_context.reset(token)
        await self._ledger(
            state,
            ActorType.POLICY,
            "policy",
            {
                "event": "policy.evaluated",
                "passed": report.passed,
                "findings": report.as_dicts(),
                "near_dup_hits": [
                    {"invoice_id": h.invoice_id, "similarity": round(h.similarity, 6)}
                    for h in near_dup_outcome.hits
                ],
            },
            policy_version=report.policy_version,
        )
        return {
            "policy": report.as_dicts(),
            "node_trace": [*state.node_trace, "policy"],
        }

    # -------------------------------------------------------------------- gate

    async def gate(self, state: GraphState) -> NodeResult:
        extraction = _extraction(state)
        match = state.match or {}
        decision = decide(
            GateInputs(
                field_confidences=extraction.confidences,
                normalized_match_delta=match.get("normalized_delta"),
                policy_severities=[f["severity"] for f in state.policy],
            )
        )
        await self._ledger(
            state,
            ActorType.SYSTEM,
            "gate",
            {"event": "gate.decided", **decision.as_dict()},
        )
        return {
            "confidence": decision.confidence,
            "gate": decision.as_dict(),
            "node_trace": [*state.node_trace, "gate"],
        }

    # ------------------------------------------------------------ auto approve

    async def auto_approve(self, state: GraphState) -> NodeResult:
        assert state.invoice_id is not None and state.run_db_id is not None
        async with self._ctx.sessions() as session:
            invoice = await session.get(Invoice, state.invoice_id)
            if invoice is not None:
                invoice.status = "AUTO_APPROVED"
            run = await session.get(Run, state.run_db_id)
            if run is not None:
                run.route = Route.AUTO.value
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.SYSTEM,
                    actor_id="auto_approve",
                    run_id=state.run_db_id,
                    invoice_id=state.invoice_id,
                    event={
                        "event": "invoice.auto_approved",
                        "confidence": state.confidence,
                        "payment": "stub-enqueued (payment execution is a non-goal)",
                    },
                ),
            )
            await session.commit()
        return {"route": Route.AUTO, "node_trace": [*state.node_trace, "auto_approve"]}

    # --------------------------------------------------------- exception triage

    async def exception_triage(self, state: GraphState) -> NodeResult:
        """Open the exception record from the run's typed findings (#30 adds
        the agent's evidence package and recommendation on top)."""
        assert state.invoice_id is not None
        entries: list[tuple[exception_taxonomy.ExceptionCode, dict[str, Any]]] = []
        for check in state.validation:
            if check["severity"] != validation.Severity.ERROR.value:
                continue
            entries.append(
                (
                    exception_taxonomy.code_for_validation(
                        validation.CheckResult(
                            validation.ReasonCode(check["code"]),
                            validation.Severity(check["severity"]),
                            check["detail"],
                            check.get("field"),
                        )
                    ),
                    check,
                )
            )
        for finding in (state.match or {}).get("findings", []):
            if finding["severity"] != matching.Severity.ERROR.value:
                continue
            entries.append(
                (
                    exception_taxonomy.code_for_matching(
                        matching.MatchFinding(
                            matching.ReasonCode(finding["code"]),
                            matching.Severity(finding["severity"]),
                            finding["detail"],
                            finding.get("line_no"),
                            finding.get("delta"),
                        )
                    ),
                    finding,
                )
            )
        for policy_finding in state.policy:  # policy codes are taxonomy codes
            entries.append(
                (exception_taxonomy.ExceptionCode(policy_finding["code"]), policy_finding)
            )
        if not entries:
            # Gate escalations (confidence < tau) arrive with no findings:
            # the exception is precisely "a human must approve this".
            gate = state.gate or {}
            if gate.get("route") != "EXCEPTION":
                raise ValueError("exception_triage reached with no findings — routing bug")
            primary = exception_taxonomy.ExceptionCode.APPROVAL_REQUIRED
            draft = exception_taxonomy.ExceptionDraft(
                invoice_id=state.invoice_id,
                run_id=state.run_db_id,
                code=primary,
                severity=exception_taxonomy.TAXONOMY[primary].severity,
                findings=(
                    {
                        "rule_id": "confidence-gate",
                        "code": primary.value,
                        "severity": "HIGH",
                        "detail": f"composite confidence {state.confidence} below tau "
                        f"{gate.get('tau')} — abstention (ADR 0003)",
                        "evidence": gate,
                    },
                ),
            )
        else:
            primary = exception_taxonomy.primary_code(code for code, _ in entries)
            draft = exception_taxonomy.ExceptionDraft(
                invoice_id=state.invoice_id,
                run_id=state.run_db_id,
                code=primary,
                severity=exception_taxonomy.TAXONOMY[primary].severity,
                findings=tuple(payload for _, payload in entries),
            )

        recommendation, triage_meta = await self._run_triage_agent(state, draft)

        async with self._ctx.sessions() as session:
            record = ExceptionRecord(
                invoice_id=state.invoice_id,
                run_id=state.run_db_id,
                type=draft.code.value,
                severity=draft.severity.value,
                evidence=draft.evidence_json(),
                recommendation=recommendation,
                status="OPEN",
                sla_due_at=exception_taxonomy.sla_due_at(self._ctx.clock(), draft.code),
            )
            session.add(record)
            await session.flush()
            invoice = await session.get(Invoice, state.invoice_id)
            if invoice is not None:
                invoice.status = "EXCEPTION"
            if state.run_db_id is not None:
                run = await session.get(Run, state.run_db_id)
                if run is not None:
                    run.route = Route.EXCEPTION.value
                    run.status = "AWAITING_DECISION"  # graph pauses here (#29)
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.SYSTEM,
                    actor_id="exception_triage",
                    run_id=state.run_db_id,
                    invoice_id=state.invoice_id,
                    event={
                        "event": "exception.opened",
                        "exception_id": record.exception_id,
                        "code": draft.code.value,
                        "severity": draft.severity.value,
                        "triage": triage_meta,
                    },
                ),
            )
            if triage_meta.get("classification") is not None:
                await writer.append(
                    session,
                    LedgerAppend(
                        actor_type=ActorType.AGENT,
                        actor_id="triage",
                        run_id=state.run_db_id,
                        invoice_id=state.invoice_id,
                        event={
                            "event": "triage.completed",
                            "exception_id": record.exception_id,
                            "classification": triage_meta["classification"],
                            "confidence": triage_meta["confidence"],
                            "abstained": triage_meta["abstained"],
                            "suggested_action": triage_meta["suggested_action"],
                        },
                        prompt_version=triage_meta.get("prompt_version"),
                    ),
                )
            await session.commit()
            return {
                "route": Route.EXCEPTION,
                "exception": {
                    "exception_id": record.exception_id,
                    "code": draft.code.value,
                    "severity": draft.severity.value,
                    "evidence": draft.evidence_json(),
                },
                "exception_id": record.exception_id,
                "node_trace": [*state.node_trace, "exception_triage"],
            }

    # ------------------------------------------------------------ human review

    async def human_review(self, state: GraphState) -> NodeResult:
        """Runs on the post-decision resume (#29): the decision itself is
        recorded transactionally by the decision service; this node audits
        the graph-side transition and closes the review bookkeeping."""
        decision = state.human_decision or {}
        await self._ledger(
            state,
            ActorType.HUMAN,
            "human_review",
            {
                "event": "human_review.completed",
                "action": decision.get("action"),
                "actor": decision.get("actor"),
                "reason_code": decision.get("reason_code"),
                "decision_id": decision.get("decision_id"),
            },
        )
        return {"node_trace": [*state.node_trace, "human_review"]}

    # ----------------------------------------------------------------- archive

    async def archive(self, state: GraphState) -> NodeResult:
        assert state.run_db_id is not None
        async with self._ctx.sessions() as session:
            run = await session.get(Run, state.run_db_id)
            if run is not None:
                run.status = "COMPLETED"
                run.finished_at = self._ctx.clock()
                if state.confidence is not None:
                    run.confidence = Decimal(str(round(state.confidence, 5)))
                if state.route is not None:
                    run.route = state.route.value
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=ActorType.SYSTEM,
                    actor_id="archive",
                    run_id=state.run_db_id,
                    invoice_id=state.invoice_id,
                    event={
                        "event": "run.archived",
                        "route": state.route.value if state.route else None,
                        "confidence": state.confidence,
                    },
                ),
            )
            await session.commit()
        return {"node_trace": [*state.node_trace, "archive"]}

    # ------------------------------------------------------------------ reject

    async def reject(self, state: GraphState) -> NodeResult:
        if state.run_db_id is not None:
            async with self._ctx.sessions() as session:
                run = await session.get(Run, state.run_db_id)
                if run is not None:
                    run.route = Route.REJECT.value
                    run.status = "REJECTED"
                await writer.append(
                    session,
                    LedgerAppend(
                        actor_type=ActorType.SYSTEM,
                        actor_id="reject",
                        run_id=state.run_db_id,
                        invoice_id=state.invoice_id,
                        event={"event": "run.rejected", "reason": "duplicate content hash"},
                    ),
                )
                await session.commit()
        return {"route": Route.REJECT, "node_trace": [*state.node_trace, "reject"]}

    # ---------------------------------------------------------------- internals

    async def _run_triage_agent(
        self, state: GraphState, draft: exception_taxonomy.ExceptionDraft
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Reasoning layer over the deterministic findings (#30). Failures
        degrade to the basic package — the human still gets the exception."""
        agent = self._ctx.triage_agent
        if agent is None:
            return None, {"classification": None, "reason": "triage agent not configured"}
        evidence = build_evidence_package(
            findings=list(draft.findings),
            extraction=state.extraction,
            match=state.match,
            exception_code=draft.code.value,
        )
        token = model_call_context.set(
            ModelCallRef(run_id=state.run_db_id, invoice_id=state.invoice_id, stage="triage")
        )
        try:
            output = await agent.triage(evidence, scenario=self._ctx.gateway_scenario)
        except Exception:
            logger.warning(
                "triage agent failed — basic package without recommendation", exc_info=True
            )
            return None, {"classification": None, "reason": "triage agent unavailable"}
        finally:
            model_call_context.reset(token)
        return output.as_exception_recommendation(), {
            "classification": output.classification,
            "confidence": output.confidence,
            "abstained": output.abstained,
            "suggested_action": output.suggested_action,
            "prompt_version": output.prompt_version,
        }

    async def _ledger(
        self,
        state: GraphState,
        actor_type: ActorType,
        actor_id: str,
        event: dict[str, Any],
        *,
        policy_version: str | None = None,
    ) -> None:
        async with self._ctx.sessions() as session:
            await writer.append(
                session,
                LedgerAppend(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    run_id=state.run_db_id,
                    invoice_id=state.invoice_id,
                    event=event,
                    policy_version=policy_version,
                    versions=VersionPins(graph=CURRENT.graph),
                ),
            )
            await session.commit()


def _extraction(state: GraphState) -> InvoiceExtraction:
    if state.extraction is None:
        raise ValueError("extraction missing — node ordering bug")
    return InvoiceExtraction.model_validate(state.extraction)


def _invoice_for_match(extraction: InvoiceExtraction) -> matching.InvoiceForMatch:
    return matching.InvoiceForMatch(
        vendor_name=extraction.vendor_name,
        invoice_number=extraction.invoice_number,
        po_number=extraction.po_number,
        currency=extraction.currency,
        issue_date=_parse_date(extraction.issue_date),
        lines=tuple(
            matching.InvoiceLineForMatch(
                line_no=line.line_no,
                qty=line.qty or Decimal("0"),
                uom=line.uom,
                unit_price=line.unit_price or Decimal("0"),
            )
            for line in extraction.lines
        ),
    )


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


async def _load_po_triple(
    session: AsyncSession, po_number: str | None
) -> tuple[PurchaseOrder | None, GoodsReceipt | None, Vendor | None]:
    if not po_number:
        return None, None, None
    po = (
        await session.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
    ).scalar_one_or_none()
    if po is None:
        return None, None, None
    gr = (
        await session.execute(select(GoodsReceipt).where(GoodsReceipt.po_id == po.po_id))
    ).scalar_one_or_none()
    vendor = await session.get(Vendor, po.vendor_id)
    return po, gr, vendor
