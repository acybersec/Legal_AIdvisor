"""
Regasire hibrida: BM25 lexical plus cautare vectoriala, fuzionate cu RRF.

De ce hibrid si nu doar semantic: textele de lege cer si potriviri exacte de
termeni si de numere de articol, unde BM25 e imbatabil, si recall semantic,
unde vectorii sunt imbatabili. Niciuna singura nu ajunge. Fuziunea prin
Reciprocal Rank Fusion evita problema scalelor incompatibile de scoruri brute:
aadună 1/(k+rang) peste liste, cu k=60. Cormack et al., SIGIR 2009.

Ruta deterministă pentru trimiteri explicite: cand intrebarea contine
"articolul N din <cod>", rezolvam direct din baza. Numerele se comporta prost
in embeddings, iar un utilizator care cere art. 145 trebuie sa primeasca
art. 145, nu ceva apropiat semantic. Este o cale de corectitudine, nu o
scurtatura de scor: metricile se raporteaza separat pe tip.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .embed import embed, incarca_index

RRF_K = 60  # constanta din lucrarea originala

# Ponderi de fuziune. RRF clasic le trateaza egal. Aici nu sunt egale, dintr-un
# motiv masurat, nu ghicit: pe intrebari in limbaj natural, lista lexicala
# intoarce frecvent articole care contin cuvintele comune ale intrebarii dar nu
# raspunsul, iar cu ponderi egale ineaca rezultatul semantic corect. Diagnostic
# concret: la "Cat este preavizul la concediere?" vectorul pune art. 75 pe
# locul 1, iar BM25 nu il intoarce deloc in primele 15.
#
# Lista lexicala ramane in fuziune, nu e scoasa, pentru ca e singura care prinde
# termeni rari si formulari exacte. Doar ca nu mai are drept de veto.
#
# ATENTIE la supra-ajustare: ponderile astea sunt o decizie de inginerie
# justificata de diagnostic, nu valori optimizate pe setul de evaluare. Setul
# are doar 20 de cazuri de continut; a le ajusta pana la scor maxim ar insemna
# sa masuram propriul reglaj, nu calitatea sistemului.
W_LEXICAL = 0.5
W_SEMANTIC = 1.0

ACTE_ALIAS = {
    "codul muncii": "codul-muncii",
    "codul fiscal": "cod-fiscal",
    "codul de procedura fiscala": "cod-procedura-fiscala",
    "codul de procedură fiscală": "cod-procedura-fiscala",
    "procedura fiscala": "cod-procedura-fiscala",
}

_ART_RE = re.compile(
    r"articol(?:ul)?\s+(\d+(?:\^\d+)?)\s*(?:din\s+(?P<act>[\w\sĀ-ɏ]+?))?\s*[?.,]?\s*$",
    re.IGNORECASE,
)


@dataclass
class Rezultat:
    articol_id: int
    numar: str
    act_slug: str
    citare: str
    cale: str
    text: str
    scor: float
    sursa: str  # "explicit" | "hibrid"


_STOP = {
    "ce", "care", "cat", "cate", "cata", "din", "pentru", "este", "sunt", "prevede",
    "poate", "pot", "sa", "se", "cu", "la", "de", "si", "un", "una", "unei", "unui",
    "mai", "daca", "imi", "mi", "am", "are", "meu", "mea", "in", "pe", "prin", "sau",
    "dupa", "fara", "asupra", "catre", "spune", "inseamna", "considera", "trebuie",
}

# Cate caractere pastram din fiecare cuvant inainte de wildcard.
_PREFIX_LEN = 6


def _stem_prefix(cuvant: str) -> str:
    """Prefix cu wildcard, ca substitut de stemmer romanesc.

    FTS5 tokenizeaza cu unicode61, care NU stemuieste. Romana e puternic
    flexionara: 'preavizul' nu se potriveste cu 'preaviz', 'concedierea' nu se
    potriveste cu 'concediere'. Fara asta, piciorul lexical rateaza tocmai
    articolele care definesc termenul cautat.

    Un stemmer romanesc adevarat, de tip Snowball, ar fi mai precis. Prefixul e
    aproximarea ieftina care rezolva majoritatea flexiunilor de substantiv si
    articol hotarat, fara dependinte noi.
    """
    return cuvant[:_PREFIX_LEN] + "*" if len(cuvant) > _PREFIX_LEN else cuvant


def _fts_query(intrebare: str) -> str:
    """Interogare FTS5 sigura, cu potrivire pe prefix pentru flexiune."""
    cuvinte = re.findall(r"\w{3,}", intrebare.lower(), re.UNICODE)
    utile = [c for c in cuvinte if c not in _STOP]
    if not utile:
        return '""'
    return " OR ".join(_stem_prefix(c) for c in utile[:14])


def detecteaza_articol(intrebare: str) -> tuple[str, str | None] | None:
    """Extrage o trimitere explicita de forma 'articolul N din <cod>'."""
    m = _ART_RE.search(intrebare.strip())
    if not m:
        return None
    act_txt = (m.group("act") or "").strip().lower().rstrip("?.,")
    slug = None
    for alias, s in ACTE_ALIAS.items():
        if alias in act_txt:
            slug = s
            break
    return m.group(1), slug


class Retriever:
    """Regasire hibrida.

    Conexiunea SQLite este per fir de executie. O singura conexiune partajata
    intre fire ridica "InterfaceError: bad parameter or other API misuse" sub
    incarcare concurenta, chiar si cu check_same_thread=False: flagul doar
    dezactiveaza verificarea, nu face conexiunea sigura la concurenta.

    Contează pentru ca backend-ul FastAPI serveste cereri in paralel, iar
    evaluarea ruleaza cu patru fire.
    """

    def __init__(self, db_path: str | Path, index_path: str | Path):
        self._db_path = str(db_path)
        self._local = threading.local()
        self.ids, self.vectors = incarca_index(index_path)

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    # -- caile individuale -------------------------------------------------

    def _explicit(self, numar: str, slug: str | None) -> list[int]:
        if slug:
            rows = self.conn.execute(
                """SELECT a.id FROM articole a JOIN acte ac USING(doc_id)
                   WHERE ac.slug = ? AND a.numar = ?""",
                (slug, numar),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id FROM articole WHERE numar = ?", (numar,)
            ).fetchall()
        return [r["id"] for r in rows]

    def _lexical(self, intrebare: str, k: int) -> list[int]:
        try:
            rows = self.conn.execute(
                """SELECT f.rowid AS id FROM articole_fts f
                   WHERE articole_fts MATCH ? ORDER BY rank LIMIT ?""",
                (_fts_query(intrebare), k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [r["id"] for r in rows]

    def _semantic(self, intrebare: str, k: int) -> list[int]:
        q = embed([intrebare])[0]
        scoruri = self.vectors @ q  # vectori normalizati, deci produsul e cosinus
        top = np.argpartition(-scoruri, min(k, len(scoruri) - 1))[:k]
        top = top[np.argsort(-scoruri[top])]
        return [int(self.ids[i]) for i in top]

    # -- fuziune -----------------------------------------------------------

    def cauta(self, intrebare: str, *, k: int = 5, pool: int = 40) -> list[Rezultat]:
        explicit = detecteaza_articol(intrebare)
        if explicit:
            ids = self._explicit(*explicit)
            if ids:
                return self._materializeaza(ids[:k], {i: 1.0 for i in ids}, "explicit")

        liste = [
            (self._lexical(intrebare, pool), W_LEXICAL),
            (self._semantic(intrebare, pool), W_SEMANTIC),
        ]
        scoruri: dict[int, float] = {}
        for lista, greutate in liste:
            for rang, aid in enumerate(lista):
                scoruri[aid] = scoruri.get(aid, 0.0) + greutate / (RRF_K + rang + 1)

        ordonate = sorted(scoruri, key=lambda i: -scoruri[i])[:k]
        return self._materializeaza(ordonate, scoruri, "hibrid")

    def _materializeaza(self, ids: list[int], scoruri: dict[int, float],
                        sursa: str) -> list[Rezultat]:
        if not ids:
            return []
        marcaje = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"""SELECT a.id, a.numar, ac.slug, a.citare, a.cale, a.text
                FROM articole a JOIN acte ac USING(doc_id) WHERE a.id IN ({marcaje})""",
            ids,
        ).fetchall()
        pe_id = {r["id"]: r for r in rows}
        out = []
        for aid in ids:
            r = pe_id.get(aid)
            if r is None:
                continue
            out.append(
                Rezultat(
                    articol_id=r["id"], numar=r["numar"], act_slug=r["slug"],
                    citare=r["citare"], cale=r["cale"], text=r["text"],
                    scor=scoruri.get(aid, 0.0), sursa=sursa,
                )
            )
        return out
