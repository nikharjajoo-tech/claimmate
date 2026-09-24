"""Mock policy administration system. All records are fictional demo data."""

from __future__ import annotations

import re
from datetime import date

from .models import ClaimFacts, PolicyLookup, PolicyRecord, is_blank

_SEED: list[PolicyRecord] = [
    PolicyRecord(
        policy_number="HO-20417",
        policyholder_name="Elena Brooks",
        policy_line="Homeowners (HO-3)",
        status="active",
        effective_start=date(2026, 1, 1),
        effective_end=date(2026, 12, 31),
        deductibles={"all_perils": 1000, "wind_hail": 2500},
        coverages=["Dwelling $540,000", "Personal property $270,000", "Water backup endorsement $15,000"],
        notes=["Water backup and sump overflow endorsement on file."],
    ),
    PolicyRecord(
        policy_number="AU-55830",
        policyholder_name="Marcus Webb",
        policy_line="Personal auto",
        status="active",
        effective_start=date(2026, 4, 1),
        effective_end=date(2026, 10, 1),
        deductibles={"collision": 500, "comprehensive": 250},
        coverages=["Bodily injury 100/300", "Collision", "Comprehensive", "Medical payments $5,000"],
    ),
    PolicyRecord(
        policy_number="RN-7702",
        policyholder_name="Aisha Karim",
        policy_line="Renters (HO-4)",
        status="active",
        effective_start=date(2025, 11, 1),
        effective_end=date(2026, 10, 31),
        deductibles={"all_perils": 250},
        coverages=["Personal property $30,000", "Theft away from premises up to 10%"],
        notes=["Theft claims require a police report number before adjuster assignment."],
    ),
    PolicyRecord(
        policy_number="TR-3391",
        policyholder_name="Daniel Ortiz",
        policy_line="Single trip travel",
        status="active",
        effective_start=date(2026, 6, 10),
        effective_end=date(2026, 6, 24),
        coverages=["Trip cancellation up to $6,000", "Trip delay $200/day after 6 hours"],
    ),
    PolicyRecord(
        policy_number="MD-4418",
        policyholder_name="Grace Liu",
        policy_line="Supplemental medical reimbursement",
        status="active",
        effective_start=date(2026, 1, 1),
        effective_end=date(2026, 12, 31),
        deductibles={"annual": 300},
        coverages=["Out-of-pocket reimbursement up to $7,500/year"],
    ),
    PolicyRecord(
        policy_number="AU-10001",
        policyholder_name="Tom Fischer",
        policy_line="Personal auto",
        status="lapsed",
        effective_start=date(2025, 8, 1),
        effective_end=date(2026, 8, 1),
        deductibles={"collision": 1000},
        coverages=["Collision", "Comprehensive"],
        notes=["Lapsed 2026-08-01 for non-payment."],
    ),
]


def normalize_policy_number(value: str) -> str:
    """Canonical lookup key: uppercase alphanumerics with every letter O folded to zero.

    Speech-to-text renders "H O dash two oh four" as "HO-2O4", "H0 204", or "ho 2 0 4".
    Records and queries are folded the same way, so either spelling matches.
    """
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper().replace("O", "0")


_INDEX: dict[str, PolicyRecord] = {normalize_policy_number(p.policy_number): p for p in _SEED}


def lookup_policy(policy_number: str) -> PolicyLookup:
    key = normalize_policy_number(policy_number)
    if is_blank(policy_number) or not key:
        return PolicyLookup(found=False, query="", message="No policy number provided yet.")
    record = _INDEX.get(key)
    if record is None:
        return PolicyLookup(
            found=False,
            query=policy_number,
            message="No policy matched. Ask the claimant to confirm it character by character.",
        )
    return PolicyLookup(found=True, query=policy_number, record=record)


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def policy_issues(facts: ClaimFacts, lookup: PolicyLookup) -> list[str]:
    """Reasons the policy needs human review. Empty list means the policy checks out.

    Checks whose input has not been collected yet are skipped; missing-field validation
    already asks for those, and comparing against a placeholder would be a false mismatch.
    """
    if not lookup.found or lookup.record is None:
        return ["Policy number could not be verified."]
    record = lookup.record
    issues: list[str] = []
    if record.status != "active":
        issues.append(f"Policy status is {record.status}.")
    if not is_blank(facts.date_of_loss):
        try:
            loss = date.fromisoformat(facts.date_of_loss)
            if not record.effective_start <= loss <= record.effective_end:
                issues.append("Loss date falls outside the policy period.")
        except ValueError:
            pass  # Date validation (INTAKE-002) reports unparseable dates.
    if not is_blank(facts.policyholder_name) and _name_key(facts.policyholder_name) != _name_key(record.policyholder_name):
        issues.append("Claimant name does not match the policyholder on record.")
    return issues
