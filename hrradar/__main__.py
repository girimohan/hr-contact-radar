from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import webbrowser
from pathlib import Path

import yaml

from . import dashboard, http, pipeline, secure

ROOT = Path(__file__).resolve().parent.parent


def load_companies(cfg: dict, cfg_path: Path) -> dict:
    """This project's company list merged with remote-job-radar's, when that one is next door."""
    merged: dict[str, list[str]] = {}
    paths = [ROOT / "data" / "companies.json"]
    if cfg.get("shared_companies"):
        paths.append((cfg_path.parent / cfg["shared_companies"]).resolve())
    sources = []
    for p in paths:
        try:
            sources.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            if p != paths[0] and cfg.get("shared_companies_url"):   # on GitHub the sister repo isn't next door
                try:
                    sources.append(http.get(cfg["shared_companies_url"], as_json=True) or {})
                except http.FetchError as e:
                    print(f"shared company list not loaded: {e}", file=sys.stderr)
    for data in sources:
        for k, v in data.items():
            if isinstance(v, list):
                merged[k] = list(dict.fromkeys(merged.get(k, []) + v))
    return merged


def cmd_run(args) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg_path = Path(args.config)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    # On a public repo, keep your own email/phone out of config.yaml: put them in the RADAR_PROFILE secret (YAML).
    extra = yaml.safe_load(os.environ.get("RADAR_PROFILE") or "") or {}
    if isinstance(extra, dict):
        cfg["profile"] = {**(cfg.get("profile") or {}), **{k: v for k, v in extra.items() if v}}
    companies = load_companies(cfg, cfg_path)
    only = set(args.sources.split(",")) if args.sources else None
    try:
        payload = pipeline.run(cfg, companies, ROOT, dt.date.today(), only, args.limit_boards,
                               enrich_sites=not args.no_sites)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    text = dashboard.digest(payload)
    (ROOT / "private" / "digest.md").write_text(text, encoding="utf-8")
    (ROOT / "private" / "notice.md").write_text(dashboard.notice(payload, os.environ.get("DASHBOARD_URL", "")),
                                                encoding="utf-8")

    f, L = payload["funnel"], payload["leads"]
    named = sum(1 for l in L if l["contacts"] and l["contacts"][0]["name"] and l["contacts"][0]["kind"] in ("posting", "ats_field"))
    print(f"read {f['collected']:,} postings -> kept {len(L):,} "
          f"({named} with a named person, {sum(1 for l in L if l['contacts'])} with someone to write to; "
          f"{payload['new_today']} new)")
    for s in payload["sources"]:
        print(f"  {s['name']:16} {'ok ' if s['ok'] else 'ERR'} read {s['fetched']:>6,} kept {s['kept']:>4}  {s['error'] or s['detail']}")
    e = payload["enrichment"]
    if e:
        print("  websites: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(e.items())))
    out = ROOT / "private" / "dashboard.html"
    print(f"Dashboard: {out}")
    if args.open:
        webbrowser.open(out.as_uri())
    if not cfg.get("profile", {}).get("name"):
        print("Tip: fill in profile.name, portfolio_url and pitch in config.yaml so the drafts are ready to send.")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(f"new_today={payload['new_today']}\nkept={len(L)}\nfirst_run={str(payload['first_run']).lower()}\n")
    return 0


def cmd_seal(args) -> int:
    """Encrypt private/dashboard.html into a page that is safe to publish."""
    html = (ROOT / "private" / "dashboard.html").read_text(encoding="utf-8")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(secure.seal_page(html, secure.passphrase_from_env()), encoding="utf-8")
    (out.parent / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Encrypted dashboard: {out} ({out.stat().st_size // 1024} KB)")
    return 0


def cmd_state(args) -> int:
    """Save or restore what a run needs the next day, encrypted (for the GitHub Actions cache)."""
    p, path = secure.passphrase_from_env(), Path(args.file)
    if args.action == "save":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secure.pack_state(ROOT, p))
        print(f"State saved: {path} ({path.stat().st_size // 1024} KB)")
        return 0
    if not path.is_file():
        print("No saved state yet; starting fresh.")
        return 0
    try:
        print("State restored: " + ", ".join(secure.unpack_state(path.read_bytes(), ROOT, p)))
    except secure.BadPassphrase:
        print("Saved state doesn't open with this passphrase (was it changed?); starting fresh.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hrradar", description="Find the person to write to for Nordic and remote openings.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect, find contacts, write private/dashboard.html")
    r.add_argument("--config", default=str(ROOT / "config.yaml"))
    r.add_argument("--sources", help="comma-separated subset, e.g. jobtech,hackernews")
    r.add_argument("--limit-boards", type=int, help="only the first N company boards / pages per source (quick test)")
    r.add_argument("--no-sites", action="store_true", help="skip reading company websites")
    r.add_argument("--open", action="store_true", help="open the dashboard when done")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("seal", help="encrypt the dashboard with RADAR_PASSPHRASE for publishing")
    s.add_argument("--out", default=str(ROOT / "site" / "index.html"))
    s.set_defaults(fn=cmd_seal)
    st = sub.add_parser("state", help="save/restore the run's state, encrypted with RADAR_PASSPHRASE")
    st.add_argument("action", choices=["save", "load"])
    st.add_argument("--file", default=str(ROOT / ".state" / "state.bin"))
    st.set_defaults(fn=cmd_state)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
