"""P05b — natural-language agreement → proposed acceptance criteria.

Flow: buyer text → AI draft (validated JSON) → deterministic review (drop ungrounded or
invented content, flag what needs configuration) → buyer accepts with explicit
configuration → an agreement in DRAFT (P03). Locking still requires the normal
propose + buyer-approval steps. The AI never locks and never touches payment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from outcomeguard.ai.gateway import AIGateway
from outcomeguard.ai.prompts import AGREEMENT_DRAFT_SYSTEM, AGREEMENT_DRAFT_VERSION, wrap_untrusted
from outcomeguard.ai.schemas import AgreementDraftOut, DraftCriterion
from outcomeguard.auth import Forbidden, Principal
from outcomeguard.domain.criteria import CriterionInput, parse_spec
from outcomeguard.models import Agreement, CriterionSource, Role
from outcomeguard.models.ai import AgreementDraft, DraftStatus
from outcomeguard.models.audit import record_audit
from outcomeguard.money import Money
from outcomeguard.services.agreements import AgreementError, AgreementService, DraftContent

MAX_REQUEST_CHARS = 5000
_KEY = re.compile(r"[^a-z0-9_]+")

# What a buyer may configure per kind when accepting a draft (never supplied by the AI).
CONFIGURABLE = {
    "http_availability": {"path", "require_https", "expected_status"},
    "mobile_viewport": {"path", "required_visible_selectors", "viewport"},
    "contact_form": {
        "path",
        "form_selector",
        "fields",
        "submit_selector",
        "success_text",
        "test_endpoint_ref",
    },
    "lighthouse_performance": {"path", "min_score", "form_factor"},
    "manual_review": {"instructions"},
}
# Contact-form defaults offered *to the buyer* (explicit opt-in), not produced by the AI.
STANDARD_FORM = {
    "fields": [
        {"selector": "input[name=name]", "value": "OutcomeGuard Verifier"},
        {"selector": "input[name=email]", "value": "verifier@example.com"},
        {"selector": "textarea[name=message]", "value": "Automated check"},
    ],
    "success_text": "Thank you",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def review_draft(requirements: str, draft: AgreementDraftOut) -> dict[str, Any]:
    """Deterministic checks on the model's proposal. Pure function."""
    text = _norm(requirements)
    numbers = set(re.findall(r"\d+", requirements))
    kept, rejected = [], []
    seen: set[str] = set()
    for c in draft.criteria:
        reasons: list[str] = []
        quote = _norm(c.source_quote)
        if not quote or quote not in text:
            reasons.append("SOURCE_QUOTE_NOT_IN_REQUEST")
        key = _KEY.sub("_", c.key.lower()).strip("_")[:50] or "criterion"
        if not re.match(r"^[a-z]", key):
            key = f"c_{key}"[:50]
        if key in seen:
            reasons.append("DUPLICATE_KEY")
        needs = list(dict.fromkeys(c.needs_configuration))
        expected = c.expected.model_dump(exclude_none=True)
        if c.kind == "lighthouse_performance":
            score = c.expected.min_performance_score
            if score is None or str(score) not in numbers or not 0 <= score <= 100:
                # A threshold the buyer did not write is not accepted from the model.
                expected.pop("min_performance_score", None)
                needs.append("lighthouse minimum performance score")
        if c.kind == "contact_form":
            for item in (
                "contact form field selectors and test values",
                "success message shown after submitting",
            ):
                if not any(item.split()[0] in n.lower() for n in needs):
                    needs.append(item)
        if c.kind == "manual_review" and not (c.manual_review_instructions or "").strip():
            c = c.model_copy(update={"manual_review_instructions": c.source_quote})
        entry = {
            "key": key,
            "title": c.title[:200],
            "kind": c.kind,
            "blocking": c.blocking,
            "source_quote": c.source_quote,
            "expected": expected,
            "needs_configuration": needs,
            "manual_review_instructions": c.manual_review_instructions,
        }
        if reasons:
            rejected.append({**entry, "reasons": reasons})
        else:
            seen.add(key)
            kept.append(entry)
    return {
        "criteria": kept,
        "rejected_criteria": rejected,
        "ambiguities": [a.model_dump() for a in draft.ambiguities],
        "manual_review_items": [m.model_dump() for m in draft.manual_review_items],
        "unsupported_requirements": [m.model_dump() for m in draft.unsupported_requirements],
        "service_summary": draft.service_summary,
        "deliverables": draft.deliverables,
    }


@dataclass
class AgreementIntelligence:
    session: Session
    gateway: AIGateway

    def draft(
        self, principal: Principal, *, buyer_organization_id: str, requirements_text: str
    ) -> AgreementDraft:
        principal.require(buyer_organization_id, Role.BUYER_ADMIN, action="drafting an agreement")
        requirements_text = requirements_text.strip()
        if not requirements_text or len(requirements_text) > MAX_REQUEST_CHARS:
            raise AgreementError(
                "INVALID_REQUEST", f"1..{MAX_REQUEST_CHARS} characters", http_status=422
            )
        result = self.gateway.call(
            task="agreement_draft",
            prompt_version=AGREEMENT_DRAFT_VERSION,
            system=AGREEMENT_DRAFT_SYSTEM,
            user=wrap_untrusted("buyer_request", requirements_text),
            schema=AgreementDraftOut,
            actor=principal.label,
        )
        if result.parsed is None:
            draft = AgreementDraft(
                buyer_organization_id=buyer_organization_id,
                created_by_user_id=principal.user_id,
                requirements_text=requirements_text,
                ai_call_id=result.call.id if result.call else None,
                status=DraftStatus.FAILED,
                ai_output=None,
                review={
                    "error": result.outcome,
                    "fallback": "AI unavailable — use the landing-page template or write "
                    "criteria manually; nothing was generated.",
                },
            )
        else:
            draft = AgreementDraft(
                buyer_organization_id=buyer_organization_id,
                created_by_user_id=principal.user_id,
                requirements_text=requirements_text,
                ai_call_id=result.call.id,
                status=DraftStatus.PROPOSED,
                ai_output=result.parsed.model_dump(),
                review=review_draft(requirements_text, result.parsed),
            )
        self.session.add(draft)
        self.session.flush()
        record_audit(
            self.session,
            actor=principal.label,
            action=f"ai.agreement_draft.{draft.status.value}",
            entity_type="agreement_draft",
            entity_id=draft.id,
            data={"ai_call_id": draft.ai_call_id, "outcome": result.outcome},
        )
        self.session.commit()
        return draft

    def accept(
        self,
        principal: Principal,
        draft_id: str,
        *,
        merchant_account_id: str,
        title: str,
        amount: Money,
        delivery_deadline: datetime,
        configuration: dict[str, dict[str, Any]],
        use_standard_form_fields: bool = False,
        include_keys: list[str] | None = None,
        blocking_overrides: dict[str, bool] | None = None,
    ) -> Agreement:
        """Buyer review: choose criteria, supply configuration, create a DRAFT agreement."""
        draft = self.session.get(AgreementDraft, draft_id, with_for_update=True)
        if draft is None or not principal.member_of(draft.buyer_organization_id):
            raise AgreementError("NOT_FOUND", "draft not found", http_status=404)
        if not principal.has(draft.buyer_organization_id, Role.BUYER_ADMIN):
            raise Forbidden("accepting a draft requires buyer_admin")
        if draft.status is not DraftStatus.PROPOSED:
            raise AgreementError("DRAFT_NOT_PROPOSED", f"draft is {draft.status.value}")

        chosen = [
            c for c in draft.review["criteria"] if include_keys is None or c["key"] in include_keys
        ]
        if not chosen:
            raise AgreementError("NO_CRITERIA", "select at least one criterion", http_status=422)
        blocking_overrides = blocking_overrides or {}
        unknown = (set(configuration) | set(blocking_overrides)) - {c["key"] for c in chosen}
        if unknown:
            raise AgreementError(
                "UNKNOWN_CRITERION", f"no such criteria: {sorted(unknown)}", http_status=422
            )

        criteria: list[CriterionInput] = []
        missing: dict[str, list[str]] = {}
        for c in chosen:
            cfg = dict(configuration.get(c["key"], {}))
            bad = set(cfg) - CONFIGURABLE[c["kind"]]
            if bad:
                raise AgreementError(
                    "INVALID_CONFIGURATION",
                    f"{c['key']}: not configurable: {sorted(bad)}",
                    http_status=422,
                )
            spec: dict[str, Any] = {"kind": c["kind"]}
            if c["kind"] == "lighthouse_performance":
                spec["min_score"] = cfg.pop("min_score", c["expected"].get("min_performance_score"))
                if c["expected"].get("form_factor"):
                    spec["form_factor"] = c["expected"]["form_factor"]
                if spec["min_score"] is None:
                    missing.setdefault(c["key"], []).append("min_score")
            if c["kind"] == "contact_form":
                if use_standard_form_fields:
                    for k, v in STANDARD_FORM.items():
                        cfg.setdefault(k, v)
                for req in ("fields", "success_text"):
                    if req not in cfg:
                        missing.setdefault(c["key"], []).append(req)
            if c["kind"] == "manual_review":
                spec["instructions"] = (
                    cfg.pop("instructions", None)
                    or c.get("manual_review_instructions")
                    or c["source_quote"]
                )
            spec.update(cfg)
            if c["key"] in missing:
                continue
            try:
                parsed = parse_spec(spec)
            except ValidationError as exc:
                raise AgreementError(
                    "INVALID_CONFIGURATION",
                    f"{c['key']}: {exc.errors()[0]['msg']}",
                    http_status=422,
                ) from exc
            criteria.append(
                CriterionInput(
                    key=c["key"],
                    title=c["title"],
                    blocking=blocking_overrides.get(c["key"], c["blocking"]),
                    spec=parsed,
                )
            )
        if missing:
            raise AgreementError(
                "CONFIGURATION_REQUIRED",
                "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items()),
                http_status=422,
            )

        agreement = AgreementService(session=self.session).create(
            principal,
            buyer_organization_id=draft.buyer_organization_id,
            merchant_account_id=merchant_account_id,
            content=DraftContent(
                title=title,
                description=draft.review.get("service_summary", ""),
                requirements_text=draft.requirements_text,
                amount=amount,
                delivery_deadline=delivery_deadline,
            ),
            criteria=criteria,
            criteria_source=CriterionSource.AI_PROPOSED,
        )
        draft = self.session.get(AgreementDraft, draft_id, with_for_update=True)
        draft.status = DraftStatus.ACCEPTED
        draft.accepted_agreement_id = agreement.id
        record_audit(
            self.session,
            actor=principal.label,
            action="ai.agreement_draft.accepted",
            entity_type="agreement_draft",
            entity_id=draft.id,
            data={
                "agreement_id": agreement.id,
                "criteria": [c.key for c in criteria],
                "standard_form_fields": use_standard_form_fields,
                "blocking_overrides": blocking_overrides,
            },
        )
        self.session.commit()
        return agreement

    def discard(self, principal: Principal, draft_id: str) -> AgreementDraft:
        draft = self.session.get(AgreementDraft, draft_id, with_for_update=True)
        if draft is None or not principal.member_of(draft.buyer_organization_id):
            raise AgreementError("NOT_FOUND", "draft not found", http_status=404)
        if not principal.has(draft.buyer_organization_id, Role.BUYER_ADMIN):
            raise Forbidden("discarding a draft requires buyer_admin")
        if draft.status is not DraftStatus.PROPOSED:
            raise AgreementError("DRAFT_NOT_PROPOSED", f"draft is {draft.status.value}")
        draft.status = DraftStatus.DISCARDED
        record_audit(
            self.session,
            actor=principal.label,
            action="ai.agreement_draft.discarded",
            entity_type="agreement_draft",
            entity_id=draft.id,
            data={},
        )
        self.session.commit()
        return draft


def criterion_kinds() -> list[str]:
    return list(CONFIGURABLE)


__all__ = ["AgreementIntelligence", "DraftCriterion", "review_draft"]
