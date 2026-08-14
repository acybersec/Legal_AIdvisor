"""
Orchestratorul de ingestie: descoperire prin SOAP, text din HTML, structurare, stocare.

Rulare:
    backend/.venv/bin/python -m app.ingest.run            # foloseste cache-ul din data/raw
    backend/.venv/bin/python -m app.ingest.run --refresh  # redescarca de pe portal

Actele sunt fixate pe doc_id, nu cautate la fiecare rulare. Motivul: cautarea dupa
titlu intoarce si versiuni abrogate, iar pentru coduri intoarce legea de aprobare
in loc de textul codului. Vezi API-LEGISLATIE.md. Fixarea e o alegere de
corectitudine, nu o scurtatura.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .parser import parse_act
from .source import _client, fetch_html, get_token, search
from .store import connect, replace_articole, upsert_act

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "legal.db"

# doc_id verificate manual 14 august 2026. Fiecare e textul CODULUI, nu legea
# de aprobare, si fiecare e versiunea in vigoare, nu una abrogata.
ACTE = [
    {"slug": "codul-muncii", "doc_id": "128647", "domeniu": "munca"},
    {"slug": "cod-fiscal", "doc_id": "171282", "domeniu": "fiscal"},
    {"slug": "cod-procedura-fiscala", "doc_id": "170007", "domeniu": "fiscal"},
]


def _meta_for(client, token, doc_id: str) -> dict | None:
    """Metadatele actului, luate din SOAP. Fara ele nu stim data intrarii in vigoare."""
    for titlu in ("Codul muncii", "Codul fiscal", "Codul de procedura fiscala"):
        for act in search(client, token, titlu=titlu, per_pagina=10):
            if act.doc_id == doc_id:
                return {
                    "doc_id": act.doc_id,
                    "titlu": act.titlu,
                    "tip_act": act.tip_act,
                    "emitent": act.emitent,
                    "data_vigoare": act.data_vigoare,
                    "link_html": act.link_html,
                }
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingestie corpus legislativ")
    ap.add_argument("--refresh", action="store_true", help="redescarca HTML-ul de pe portal")
    args = ap.parse_args(argv)

    RAW.mkdir(parents=True, exist_ok=True)
    conn = connect(DB)
    client = _client()
    token = get_token(client)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    total = 0
    for spec in ACTE:
        slug, doc_id = spec["slug"], spec["doc_id"]
        cached = RAW / f"{slug}.html"

        if args.refresh or not cached.exists():
            html = fetch_html(client, doc_id)
            cached.write_text(html, encoding="utf-8")
        else:
            html = cached.read_text(encoding="utf-8", errors="replace")

        articole = parse_act(html)
        if not articole:
            # Regula din brief: nu inventa, opreste-te si raporteaza.
            print(f"EROARE: {slug} doc={doc_id} a produs 0 articole. Verifica documentul.",
                  file=sys.stderr)
            return 1

        meta = _meta_for(client, token, doc_id)
        if meta is None:
            print(f"EROARE: nu am gasit metadate SOAP pentru doc={doc_id}.", file=sys.stderr)
            return 1
        meta |= {"slug": slug, "in_vigoare": 1}

        upsert_act(conn, meta, now)
        n = replace_articole(conn, doc_id, meta["titlu"], articole)
        total += n
        print(f"{slug:24s} doc={doc_id:>7s} vigoare={meta['data_vigoare'][:10]} "
              f"articole={n:4d} HTML={len(html):>9,d} oct")

    print(f"\nTotal articole in baza: {total}")
    print(f"Baza: {DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
