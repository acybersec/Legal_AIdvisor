"""
Persistenta corpusului. SQLite cu FTS5 pentru MVP-ul local.

Doua decizii care conteaza pentru corectitudinea produsului:

1. Citarea se compune AICI, la ingestie, si se stocheaza ca text. Modelul nu
   construieste niciodata un sir de citare. Vezi ISC-3 din PLAN-RALPH.md.

2. Indexam si varianta fara diacritice. Fara asta, o intrebare scrisa "concediu
   de odihna" nu gaseste "concediu de odihnă".
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .parser import Articol, normalize, strip_diacritics

SCHEMA = """
CREATE TABLE IF NOT EXISTS acte (
    doc_id        TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    titlu         TEXT NOT NULL,
    tip_act       TEXT NOT NULL,
    emitent       TEXT,
    data_vigoare  TEXT,
    link_html     TEXT NOT NULL,
    in_vigoare    INTEGER NOT NULL DEFAULT 1,
    descarcat_la  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articole (
    id            INTEGER PRIMARY KEY,
    doc_id        TEXT NOT NULL REFERENCES acte(doc_id) ON DELETE CASCADE,
    numar         TEXT NOT NULL,
    eticheta      TEXT NOT NULL,
    denumire      TEXT,
    text          TEXT NOT NULL,
    text_plat     TEXT NOT NULL,   -- fara diacritice, pentru cautare toleranta
    cale          TEXT NOT NULL,
    titlu_parinte TEXT,
    capitol       TEXT,
    sectiune      TEXT,
    citare        TEXT NOT NULL,   -- sirul de citare, compus la ingestie
    alineate_json TEXT NOT NULL,
    note_json     TEXT NOT NULL,
    referinte_json TEXT NOT NULL,
    UNIQUE(doc_id, numar)
);

CREATE INDEX IF NOT EXISTS idx_articole_doc ON articole(doc_id);
CREATE INDEX IF NOT EXISTS idx_articole_numar ON articole(numar);

CREATE VIRTUAL TABLE IF NOT EXISTS articole_fts USING fts5(
    text, text_plat, denumire, cale,
    content='articole', content_rowid='id', tokenize='unicode61'
);
"""

# Trigger-ele tin indexul FTS sincronizat fara sa ne bazam pe disciplina apelantului.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS articole_ai AFTER INSERT ON articole BEGIN
  INSERT INTO articole_fts(rowid, text, text_plat, denumire, cale)
  VALUES (new.id, new.text, new.text_plat, new.denumire, new.cale);
END;
CREATE TRIGGER IF NOT EXISTS articole_ad AFTER DELETE ON articole BEGIN
  INSERT INTO articole_fts(articole_fts, rowid, text, text_plat, denumire, cale)
  VALUES ('delete', old.id, old.text, old.text_plat, old.denumire, old.cale);
END;
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.executescript(TRIGGERS)
    return conn


def build_citare(titlu_act: str, articol: Articol) -> str:
    """Sirul de citare, compus determinist din date stocate.

    Nicio parte din el nu vine de la un model. Asta e garantia ISC-3: chiar daca
    modelul halucineaza, citarea afisata provine din baza de date.
    """
    scurt = normalize(titlu_act).split("(")[0].strip().rstrip(",")
    # Titlul din SOAP vine cu "EMITENT: ..." lipit; taiem la prima eticheta.
    scurt = re.split(r"\s+EMITEN[TŢȚ]", scurt)[0].strip()
    return f"{articol.eticheta} din {scurt}"


def upsert_act(conn: sqlite3.Connection, meta: dict, descarcat_la: str) -> None:
    conn.execute(
        """INSERT INTO acte (doc_id, slug, titlu, tip_act, emitent, data_vigoare,
                             link_html, in_vigoare, descarcat_la)
           VALUES (:doc_id, :slug, :titlu, :tip_act, :emitent, :data_vigoare,
                   :link_html, :in_vigoare, :descarcat_la)
           ON CONFLICT(doc_id) DO UPDATE SET
             titlu=excluded.titlu, data_vigoare=excluded.data_vigoare,
             in_vigoare=excluded.in_vigoare, descarcat_la=excluded.descarcat_la""",
        meta | {"descarcat_la": descarcat_la},
    )


def replace_articole(conn: sqlite3.Connection, doc_id: str, titlu_act: str,
                     articole: list[Articol]) -> int:
    """Rescrie articolele unui act. Idempotenta: o reingestie da acelasi rezultat."""
    conn.execute("DELETE FROM articole WHERE doc_id = ?", (doc_id,))
    rows = []
    for a in articole:
        rows.append(
            (
                doc_id, a.numar, a.eticheta, a.denumire, a.text,
                strip_diacritics(a.text), a.cale(),
                a.parinti.get("Titlu"), a.parinti.get("Capitol"), a.parinti.get("Sectiune"),
                build_citare(titlu_act, a),
                json.dumps([{"numar": x.numar, "text": x.text} for x in a.alineate],
                           ensure_ascii=False),
                json.dumps(a.note, ensure_ascii=False),
                json.dumps(a.referinte, ensure_ascii=False),
            )
        )
    conn.executemany(
        """INSERT INTO articole (doc_id, numar, eticheta, denumire, text, text_plat,
               cale, titlu_parinte, capitol, sectiune, citare, alineate_json,
               note_json, referinte_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return len(rows)
