"""Mock policy administration system. All records are fictional demo data."""

from __future__ import annotations

import re
from datetime import date

from .models import ClaimFacts, PolicyLookup, PolicyRecord, Product, is_blank

_SEED: list[PolicyRecord] = [
    PolicyRecord(
        policy_number="HO-20417",
        policyholder_name="Elena Brooks",
        policy_line="Homeowners (HO-3)",
        product=Product.HOMEOWNERS,
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
        product=Product.AUTO,
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
        product=Product.RENTERS,
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
        product=Product.TRAVEL,
        status="active",
        effective_start=date(2026, 9, 5),
        effective_end=date(2026, 9, 30),
        coverages=["Trip cancellation up to $6,000", "Trip delay $200/day after 6 hours"],
    ),
    PolicyRecord(
        policy_number="MD-4418",
        policyholder_name="Grace Liu",
        policy_line="Supplemental medical reimbursement",
        product=Product.MEDICAL,
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
        product=Product.AUTO,
        status="lapsed",
        effective_start=date(2025, 8, 1),
        effective_end=date(2026, 8, 1),
        deductibles={"collision": 1000},
        coverages=["Collision", "Comprehensive"],
        notes=["Lapsed 2026-08-01 for non-payment."],
    ),
    PolicyRecord(
        policy_number="HO-31552",
        policyholder_name="Priya Nair",
        policy_line="Homeowners (HO-3)",
        product=Product.HOMEOWNERS,
        status="active",
        effective_start=date(2026, 2, 1),
        effective_end=date(2027, 1, 31),
        deductibles={"all_perils": 1500},
        coverages=["Dwelling $410,000", "Personal property $205,000"],
    ),
    PolicyRecord(
        policy_number="AU-72214",
        policyholder_name="Sofia Martinez",
        policy_line="Personal auto",
        product=Product.AUTO,
        status="active",
        effective_start=date(2026, 2, 1),
        effective_end=date(2027, 2, 1),
        deductibles={"collision": 500, "comprehensive": 500},
        coverages=["Bodily injury 50/100", "Collision", "Comprehensive", "Rental $35/day"],
    ),
    PolicyRecord(
        policy_number="AU-66001",
        policyholder_name="Omar Haddad",
        policy_line="Personal auto",
        product=Product.AUTO,
        status="active",
        effective_start=date(2026, 1, 15),
        effective_end=date(2027, 1, 15),
        deductibles={"collision": 750, "comprehensive": 250},
        coverages=["Collision", "Comprehensive", "Medical payments $2,000"],
    ),
    PolicyRecord(
        policy_number="RN-8120",
        policyholder_name="James Okafor",
        policy_line="Renters (HO-4)",
        product=Product.RENTERS,
        status="active",
        effective_start=date(2026, 3, 1),
        effective_end=date(2027, 2, 28),
        deductibles={"all_perils": 500},
        coverages=["Personal property $20,000", "Loss of use $6,000"],
    ),
    PolicyRecord(
        policy_number="MD-5093",
        policyholder_name="Kevin Tran",
        policy_line="Supplemental medical reimbursement",
        product=Product.MEDICAL,
        status="active",
        effective_start=date(2026, 1, 1),
        effective_end=date(2026, 12, 31),
        deductibles={"annual": 500},
        coverages=["Out-of-pocket reimbursement up to $5,000/year"],
    ),
    PolicyRecord(
        policy_number="TR-5520",
        policyholder_name="Nina Patel",
        policy_line="Single trip travel",
        product=Product.TRAVEL,
        status="active",
        effective_start=date(2026, 8, 1),
        effective_end=date(2026, 8, 15),
        coverages=["Trip cancellation up to $4,000", "Baggage delay $300"],
    ),
    PolicyRecord(
        policy_number="HO-40090",
        policyholder_name="Laura Chen",
        policy_line="Homeowners (HO-3)",
        product=Product.HOMEOWNERS,
        status="cancelled",
        effective_start=date(2025, 5, 1),
        effective_end=date(2026, 5, 1),
        coverages=["Dwelling $380,000"],
        notes=["Cancelled at insured's request effective 2026-05-01."],
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


def _tokens(name: str) -> list[str]:
    return [part for part in (re.sub(r"[^a-z]", "", t) for t in str(name or "").lower().split()) if part]


def _soundex(word: str) -> str:
    """Classic Soundex: first letter plus three consonant codes, vowels dropped.

    Speech-to-text confuses names that sound alike, which is exactly what Soundex equates:
    "Liu" and "Lee" are both L000.
    """
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
             "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    if not word:
        return ""
    out, previous = word[0].upper(), codes.get(word[0], "")
    for char in word[1:]:
        code = codes.get(char, "")
        if code and code != previous:
            out += code
        if char not in "hw":  # h and w are transparent: they do not break a repeat
            previous = code
    return (out + "000")[:4]


def _distance(a: str, b: str) -> int:
    """Levenshtein distance, iterative and small; no dependency worth adding for this."""
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _close(spoken: str, recorded: str) -> bool:
    """Same name, heard imperfectly.

    Both tests must pass. Soundex alone is too generous for given names ("John" and "Jane" are
    both J500); edit distance alone would equate short unrelated names. Together they accept
    Lee/Liu (same sound, two edits) and reject John/Jane (same sound, three edits).
    """
    return spoken == recorded or (_soundex(spoken) == _soundex(recorded) and _distance(spoken, recorded) <= 2)


def names_match(spoken: str, recorded: str) -> bool:
    """Whether the caller's name is the policyholder's, allowing for how it was heard.

    An exact-letter comparison treats a transcription slip as a possible impostor: it reported
    "Grace Lee" against a policy reading "Grace Liu" with the same confidence as a different
    person entirely, which sent honest claims to policy review.
    """
    said, record = _tokens(spoken), _tokens(recorded)
    if _name_key(spoken) == _name_key(recorded):
        return True
    if len(said) < 2 or len(record) < 2:
        # A partial name ("Liu" alone) is not accepted as a match; the whole name is still asked for.
        return _close(_name_key(spoken), _name_key(recorded))
    # Middle names come and go, so only the first and last are compared.
    return _close(said[0], record[0]) and _close(said[-1], record[-1])


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
    if not is_blank(facts.policyholder_name) and not names_match(facts.policyholder_name, record.policyholder_name):
        issues.append("Claimant name does not match the policyholder on record.")
    return issues
