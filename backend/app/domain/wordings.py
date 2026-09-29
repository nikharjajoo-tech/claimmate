"""Policy wordings: the rulebook a claim is reviewed against (PRD D14c, FR-10.1).

The declarations page (`PolicyRecord`) says what this customer bought; the wording says what the
product covers, excludes, and requires. One wording per product, shared by every customer on it.

Each wording carries an id and a version (`medical/v1`). A policy review stores that reference, so
editing a wording later can never silently change what the section numbers in an older review
pointed at: a changed wording is a new version.

File format (`wordings/<product>-<version>.md`): YAML front matter, then

    ## 2. What we pay          <- a part: heading only, no body text of its own
    ### 2.1 Eligible costs     <- a clause: the citable unit, with the body text

All documents here are fictional demo data.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Product

WORDING_DIR = Path(__file__).resolve().parent / "wordings"

_PART = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
_CLAUSE = re.compile(r"^###\s+(\d+\.\d+)\s+(.+?)\s*$")


class PolicyClause(BaseModel):
    """One numbered clause: the unit a policy review may cite."""

    number: str  # "2.1"
    heading: str
    part: str  # heading of the part it sits under, for display
    text: str

    @property
    def label(self) -> str:
        return f"§{self.number} {self.heading}"


class PolicyWording(BaseModel):
    wording_id: str
    version: str
    product: Product
    title: str
    effective: str = ""
    clauses: list[PolicyClause] = Field(default_factory=list)

    @property
    def ref(self) -> str:
        """What a stored review records, e.g. 'medical/v1'."""
        return f"{self.wording_id}/{self.version}"

    @property
    def numbers(self) -> list[str]:
        return [c.number for c in self.clauses]

    def clause(self, number: str) -> PolicyClause | None:
        """Look up a cited clause. '§2.1' and '2.1 ' are the same citation."""
        wanted = str(number or "").strip().lstrip("§").strip().rstrip(".")
        return next((c for c in self.clauses if c.number == wanted), None)

    def as_prompt_text(self) -> str:
        """The whole wording as it goes into the review prompt (2-4 pages, no index needed)."""
        lines = [f"{self.title} ({self.ref})"]
        for clause in self.clauses:
            lines.append(f"\n{clause.number} {clause.heading}\n{clause.text}")
        return "\n".join(lines)


def _split_front_matter(text: str, path: Path) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing YAML front matter")
    _, meta, body = text.split("---\n", 2)
    return yaml.safe_load(meta) or {}, body


def parse_wording(text: str, path: Path) -> PolicyWording:
    meta, body = _split_front_matter(text, path)
    clauses: list[PolicyClause] = []
    part_heading = ""
    part_number = ""
    current: PolicyClause | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None:
            current.text = "\n".join(buffer).strip()
            if not current.text:
                raise ValueError(f"{path.name}: clause {current.number} has no text")
            clauses.append(current)

    for line in body.splitlines():
        if part := _PART.match(line):
            flush()
            current, buffer = None, []
            part_number, part_heading = part.group(1), part.group(2)
        elif clause := _CLAUSE.match(line):
            flush()
            number, heading = clause.group(1), clause.group(2)
            if number.split(".")[0] != part_number:
                raise ValueError(f"{path.name}: clause {number} is not under part {part_number or '(none)'}")
            current, buffer = PolicyClause(number=number, heading=heading, part=part_heading, text=""), []
        elif line.startswith("#"):
            raise ValueError(f"{path.name}: unexpected heading {line!r}; use '## N. part' or '### N.M clause'")
        elif current is None:
            # Body text directly under a part would be citable text nothing can cite.
            if line.strip():
                raise ValueError(f"{path.name}: text outside a numbered clause: {line.strip()[:60]!r}")
        else:
            buffer.append(line)
    flush()

    if not clauses:
        raise ValueError(f"{path.name}: no clauses found")
    duplicates = {n for n in (c.number for c in clauses) if [c.number for c in clauses].count(n) > 1}
    if duplicates:
        raise ValueError(f"{path.name}: duplicate clause numbers {sorted(duplicates)}")
    return PolicyWording(**meta, clauses=clauses)


@lru_cache
def load_wordings(directory: Path = WORDING_DIR) -> dict[Product, PolicyWording]:
    wordings: dict[Product, PolicyWording] = {}
    for path in sorted(directory.glob("*.md")):
        wording = parse_wording(path.read_text(), path)
        if wording.product in wordings:
            raise ValueError(f"{path.name}: a second wording for product {wording.product}")
        wordings[wording.product] = wording
    missing = [p for p in Product if p not in wordings]
    if missing:
        raise ValueError(f"no policy wording for {[p.value for p in missing]}")
    return wordings


def wording_for(product: Product) -> PolicyWording:
    return load_wordings()[product]
