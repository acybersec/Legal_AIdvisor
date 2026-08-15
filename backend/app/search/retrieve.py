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

import os
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

# Cati candidati vede reordonarea. Opt, nu mai multi, si nu mai putini.
#
# Mai putini: articolul corect statea pe locul OPT in cazul care a motivat tot
# pasul asta, deci o fereastra de cinci l-ar fi ratat.
# Mai multi: promptul creste liniar, iar castigul nu - ce nu intra in primele
# opt dupa RRF e aproape intotdeauna dintr-un cu totul alt subiect.
_POOL_RERANK = 8

# Reordonarea e OPRITA implicit, si asta e o concluzie masurata, nu o omisiune.
#
# Cifrele complete, pe aceleasi 105 cazuri:
#
#   varianta                 raspunsuri false   refuzuri false   corecte
#   fara reordonare                    2               0            93
#   doar reordonare                    4               2            92
#   reordonare + poarta                1               3            90
#
# Reordonarea + poarta chiar injumatatesc raspunsurile false, de la 2 la 1. Dar
# refuza gresit 3 intrebari valide din 20, adica 15% dintre intrebarile reale
# ale unui client primesc "nu pot raspunde" cand raspunsul exista. Pentru un
# produs vandut pe abonament asta costa mai mult decat castiga.
#
# Codul ramane, complet si testat, fiindca pe un corpus mai mare sau cu un model
# de reordonare mai bun raportul se poate inversa. Se aprinde cu RERANK=1 si se
# remasoara cu app.eval.end_to_end.
RERANK_IMPLICIT = os.environ.get("RERANK", "0").strip() not in ("", "0")


# Intrebari despre valoarea IN VIGOARE ACUM. Deterministe, fara model.
#
# Corpusul e o fotografie facuta la data ingestiei. La "cat e salariul minim in
# 2026" raspunsul corect nu e un articol, e "nu pot sti asta din textul legii",
# si asta se stie din INTREBARE, nu din candidati. Un model nu adauga nimic
# aici, si masurat chiar strica: cu extrase mai lungi, gasea art. 291 care chiar
# vorbeste despre cote reduse de TVA si declara intrebarea acoperita, ignorand
# ca se ceruse valoarea de azi.
#
# Regula, nu model, din acelasi motiv pentru care citarile se leaga in cod:
# ce poate fi impus determinist nu se lasa pe seama bunavointei unui model.
#
# Atentie la ce NU trebuie sa prinda. Doua intrebari legitime din setul de
# evaluare vorbesc despre exact aceleasi notiuni fara sa ceara valoarea de azi:
#   "Cine stabileste salariul minim pe economie si cand se aplica?"
#   "Ce se intampla daca o microintreprindere depaseste plafonul de venituri?"
# De aceea tiparul cere un marcaj temporal explicit, nu cuvinte de subiect.
_VALOARE_LA_ZI = re.compile(
    r"\b(?:in\s+(?:acest\s+(?:an|moment)|momentul\s+de\s+fata|prezent|vigoare\s+acum)"
    r"|anul\s+acesta|in\s+20\d\d|actualmente|la\s+ora\s+actuala|in\s+ziua\s+de\s+azi)\b",
    re.IGNORECASE,
)


def cere_valoare_la_zi(intrebare: str) -> bool:
    """Intrebarea cere o valoare in vigoare acum, pe care un corpus fix nu o poate da."""
    return bool(_VALOARE_LA_ZI.search(intrebare))


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


# --- Intrebari de definitie -------------------------------------------------
#
# PROBLEMA, masurata: "Ce se considera timp de munca?" scotea art. 111 pe locul
# opt din opt, desi art. 111 spune textual "Timpul de munca reprezinta orice
# perioada in care salariatul presteaza munca". Deasupra stateau articole care
# FOLOSESC termenul de mai multe ori.
#
# Cauza e felul in care functioneaza BM25: scorul creste cu frecventa. Un
# articol care foloseste repetat un termen bate articolul care il DEFINESTE o
# singura data. Pentru un corpus juridic e sistematic, fiindca definitiile sunt
# scurte si enunta termenul o data.
#
# De ce NU un reranker cu model: a fost construit si masurat. Reordoneaza bine,
# dar produce INTOTDEAUNA un "cel mai bun", deci raspunde si la intrebari la
# care sistemul ar fi trebuit sa taca. Vezi rerank.py.
#
# Semnalul folosit aici e ingust si verificabil: doar 64 din cele 1372 de
# articole deschid cu o constructie de definitie. Nu e o euristica vaga peste
# tot corpusul, e o potrivire pe o forma juridica standard.
_INTREBARE_DEFINITIE = re.compile(
    r"^\s*(?:ce\s+(?:se\s+(?:considera|intelege|în\s*țelege|înțelege)|este|inseamna"
    r"|înseamnă|reprezinta|reprezintă)|definiti[ae]\s+(?:lui\s+)?)\s+(?P<termen>.+?)\s*[?.]?\s*$",
    re.IGNORECASE,
)

# Verbele cu care legea romaneasca introduce o definitie.
_VERB_DEFINITIE = re.compile(
    r"(?:reprezint[ăa]|se\s+[îi]n[țt]elege|constituie|este\s+definit)", re.IGNORECASE
)

# Cat din inceputul articolului privim. O definitie sta in alineatul (1), nu la
# mijloc; cautand mai departe am prinde trimiteri intamplatoare.
_FEREASTRA_DEFINITIE = 200

# Bonusul adaugat scorului RRF. Ordinul de marime conteaza: scorurile RRF sunt
# in jur de 1/(60+rang), deci intre 0,016 si 0,014 pe primele pozitii. Un bonus
# de 0,02 muta un articol de definitie peste tot restul, dar NU inventeaza
# rezultate: se aplica doar candidatilor deja regasiti.
_BONUS_DEFINITIE = 0.02


def detecteaza_definitie(intrebare: str) -> str | None:
    """Termenul cerut, daca intrebarea cere o definitie. Altfel None."""
    m = _INTREBARE_DEFINITIE.match(intrebare.strip())
    return m.group("termen").strip() if m else None


def _defineste(text: str, termen: str) -> bool:
    """Deschide articolul cu definitia termenului cerut?

    Potrivirea pe cuvinte se face pe PREFIX, din acelasi motiv pentru care o
    face si cautarea lexicala: romana e flexionara, iar intrebarea spune "timp
    de munca" acolo unde legea scrie "Timpul de munca".
    """
    inceput = text[:_FEREASTRA_DEFINITIE]
    if not _VERB_DEFINITIE.search(inceput):
        return False
    cuvinte = [c for c in re.findall(r"\w{3,}", termen.lower(), re.UNICODE) if c not in _STOP]
    if not cuvinte:
        return False
    jos = inceput.lower()
    return all(c[:_PREFIX_LEN] in jos for c in cuvinte)


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

    def cauta(self, intrebare: str, *, k: int = 5, pool: int = 40,
              rerank: bool = RERANK_IMPLICIT) -> list[Rezultat]:
        # Intrebarile despre valoarea in vigoare ACUM nu au raspuns intr-un
        # corpus care e o fotografie, si asta se stie din intrebare. Lista goala
        # inseamna mai jos in lant refuz explicit. Vezi rerank.cere_valoare_la_zi.
        if cere_valoare_la_zi(intrebare):
            return []

        explicit = detecteaza_articol(intrebare)
        if explicit:
            ids = self._explicit(*explicit)
            if ids:
                # Ruta determinista nu se reordoneaza. Intrebarea numeste
                # articolul, deci nu exista nimic de judecat, iar un apel de
                # model aici ar adauga secunde si risc fara castig.
                return self._materializeaza(ids[:k], {i: 1.0 for i in ids}, "explicit")

        liste = [
            (self._lexical(intrebare, pool), W_LEXICAL),
            (self._semantic(intrebare, pool), W_SEMANTIC),
        ]
        scoruri: dict[int, float] = {}
        for lista, greutate in liste:
            for rang, aid in enumerate(lista):
                scoruri[aid] = scoruri.get(aid, 0.0) + greutate / (RRF_K + rang + 1)

        # Cand urmeaza reordonarea, materializam mai multi candidati decat cere
        # apelantul. Altfel reordonarea ar putea doar sa amestece primii k, iar
        # articolul corect - care sta uneori pe locul opt, vezi rerank.py - nu
        # ar ajunge niciodata sub ochii modelului.
        cati = _POOL_RERANK if rerank else k
        # Intrebare de definitie: articolul care DEFINESTE termenul urca peste
        # cele care doar il folosesc. Determinist, fara apel de model.
        termen = detecteaza_definitie(intrebare)
        if termen and scoruri:
            for aid, text_art in self._texte(list(scoruri)).items():
                if _defineste(text_art, termen):
                    scoruri[aid] += _BONUS_DEFINITIE

        ordonate = sorted(scoruri, key=lambda i: -scoruri[i])[:cati]
        rezultate = self._materializeaza(ordonate, scoruri, "hibrid")

        if not rerank:
            return rezultate[:k]
        from .rerank import rerankeaza  # import local: lantul de baza nu depinde de model
        return rerankeaza(intrebare, rezultate, k=k)

    def _texte(self, ids: list[int]) -> dict[int, str]:
        """Doar inceputul textului, pentru verificarea de definitie."""
        if not ids:
            return {}
        marcaje = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id, substr(text, 1, {_FEREASTRA_DEFINITIE}) AS t "
            f"FROM articole WHERE id IN ({marcaje})",
            ids,
        ).fetchall()
        return {r["id"]: r["t"] for r in rows}

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
