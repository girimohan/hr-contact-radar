"""One module per source. Each fetch function returns a SourceResult of Leads."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Lead


@dataclass
class SourceResult:
    name: str
    leads: list[Lead] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    detail: str = ""
