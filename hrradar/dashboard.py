from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def build(payload: dict) -> str:
    """The dashboard as one self-contained file, data embedded, so it opens with a double-click."""
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data, 1)


def digest(payload: dict, limit: int = 25) -> str:
    """Today's new leads with a contact, as Markdown (for a terminal or a private issue)."""
    new = [l for l in payload["leads"] if l["first_seen"] == payload["date"] and l["contacts"]]
    lines = [f"# HR contact radar {payload['date']}", "",
             f"{len(payload['leads'])} openings kept, {payload['new_today']} new since the last run, "
             f"{sum(1 for l in payload['leads'] if l['contacts'])} with someone to write to.", ""]
    for l in new[:limit]:
        c = l["contacts"][0]
        who = c["name"] or c["email"]
        lines.append(f"- **{l['title']}** at {l['company']} ({l['country']}, score {l['score']}): "
                     f"{who}{', ' + c['role'] if c['role'] and c['name'] else ''}"
                     f"{' (address guessed)' if c['email_guessed'] else ''}. {l['url']}")
    if len(new) > limit:
        lines.append(f"- and {len(new) - limit} more in the dashboard")
    return "\n".join(lines) + "\n"
