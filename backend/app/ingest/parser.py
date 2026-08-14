"""
Parser pentru paginile de act de pe legislatie.just.ro.

De ce HTML si nu API-ul SOAP: API-ul intoarce actul la data republicarii, nu
consolidat la zi. Vezi API-LEGISLATIE.md. Pagina publica are forma consolidata,
iar portalul o marcheaza semantic cu clase S_*, deci parsarea e determinista,
nu euristica.

Ierarhia din pagina:
    S_TTL  Titlu     -> S_TTL_TTL (eticheta), S_TTL_DEN (denumire), S_TTL_BDY
    S_CAP  Capitol   -> idem
    S_SEC  Sectiune  -> idem
    S_ART  Articol   -> S_ART_TTL, S_ART_DEN, S_ART_BDY
    S_ALN  Alineat   -> S_ALN_TTL (ex. "(1)"), S_ALN_BDY
    S_LIT  Litera    -> S_LIT_TTL, S_LIT_BDY
    S_NTA  Nota      -> note de modificare, atasate articolului
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict

from bs4 import BeautifulSoup, Tag

# Nivelurile de ierarhie de deasupra articolului, in ordine descrescatoare.
HIERARCHY = ("S_TTL", "S_CAP", "S_SEC")

# Diacriticele romanesti apar in sursa si cu sedila, si cu virgula dedesubt.
# Forma corecta e cea cu virgula. Fara normalizare, cautarea rateaza jumatate
# din potriviri. Vezi API-LEGISLATIE.md.
_DIACRITIC_FIXES = {
    "ş": "ș",  # ş -> ș
    "Ş": "Ș",  # Ş -> Ș
    "ţ": "ț",  # ţ -> ț
    "Ţ": "Ț",  # Ţ -> Ț
}


def normalize(text: str) -> str:
    """Normalizeaza diacriticele si spatiile albe. Idempotenta."""
    text = unicodedata.normalize("NFC", text)
    for bad, good in _DIACRITIC_FIXES.items():
        text = text.replace(bad, good)
    # BOM si spatii zero-width scapa din sursa si ajung vizibile in citari.
    text = text.translate({0xFEFF: None, 0x200B: None, 0x200C: None, 0x200D: None})
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def strip_diacritics(text: str) -> str:
    """Varianta fara diacritice, pentru cautare tolerantă."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass
class Alineat:
    numar: str  # "(1)", "(2)" — asa cum apare in act
    text: str


@dataclass
class Articol:
    numar: str  # "55" — numarul extras
    eticheta: str  # "Articolul 55" — cum apare in act
    denumire: str  # denumirea marginala, poate fi goala
    text: str  # textul complet, alineatele concatenate
    alineate: list[Alineat] = field(default_factory=list)
    parinti: dict[str, str] = field(default_factory=dict)  # {"Titlu": "...", ...}
    note: list[str] = field(default_factory=list)  # note de modificare
    referinte: list[str] = field(default_factory=list)  # trimiteri la alte acte

    def cale(self) -> str:
        """Calea ierarhica lizibila, pentru afisare si pentru citare."""
        parts = [self.parinti[k] for k in ("Titlu", "Capitol", "Sectiune") if k in self.parinti]
        return " > ".join(parts + [self.eticheta])


_LEVEL_NAMES = {"S_TTL": "Titlu", "S_CAP": "Capitol", "S_SEC": "Sectiune"}


def _label_of(node: Tag, level: str) -> str:
    """Eticheta plus denumirea unui nivel de ierarhie, ex. 'Titlul II Contractul...'."""
    ttl = node.find(class_=f"{level}_TTL", recursive=False)
    den = node.find(class_=f"{level}_DEN", recursive=False)
    bits = [normalize(n.get_text(" ", strip=True)) for n in (ttl, den) if n]
    return " ".join(b for b in bits if b)


def _ancestors(art: Tag) -> dict[str, str]:
    """Lantul de parinti al unui articol, de la Titlu in jos."""
    found: dict[str, str] = {}
    for parent in art.parents:
        classes = parent.get("class") or []
        for level in HIERARCHY:
            if level in classes and _LEVEL_NAMES[level] not in found:
                label = _label_of(parent, level)
                if label:
                    found[_LEVEL_NAMES[level]] = label
    # Ordoneaza de sus in jos, indiferent de ordinea de descoperire.
    return {name: found[name] for name in ("Titlu", "Capitol", "Sectiune") if name in found}


def _extract_notes(art: Tag) -> list[str]:
    """Scoate notele din arbore si le intoarce separat.

    Notele sunt comentarii despre act — decizii CCR, trimiteri la actele
    modificatoare — nu text de lege. Daca raman in corpul articolului, ajung
    citate ca si cum ar fi norma. Le detasam INAINTE de extragerea textului.
    """
    notes: list[str] = []
    for nta in art.find_all(class_="S_NTA"):
        text = normalize(nta.get_text(" ", strip=True))
        if text:
            notes.append(text)
        nta.decompose()
    return notes


def _literele_of(art: Tag) -> list[Alineat]:
    """Literele si punctele care nu stau sub un alineat, ex. 'a) de drept;'."""
    out: list[Alineat] = []
    for lit in art.find_all(class_=["S_LIT", "S_PCT", "S_LIN"]):
        if lit.find_parent(class_="S_ALN") is not None:
            continue  # apartine unui alineat, e deja acoperit acolo
        ttl = lit.find(class_=re.compile(r"S_(LIT|PCT|LIN)_TTL"))
        bdy = lit.find(class_=re.compile(r"S_(LIT|PCT|LIN)_BDY"))
        if bdy is None:
            continue
        out.append(
            Alineat(
                numar=normalize(ttl.get_text(" ", strip=True)) if ttl else "",
                text=normalize(bdy.get_text(" ", strip=True)),
            )
        )
    return out


def _alineate_of(art: Tag) -> list[Alineat]:
    out: list[Alineat] = []
    for aln in art.find_all(class_="S_ALN"):
        ttl = aln.find(class_="S_ALN_TTL")
        bdy = aln.find(class_="S_ALN_BDY")
        if bdy is None:
            continue
        out.append(
            Alineat(
                numar=normalize(ttl.get_text(" ", strip=True)) if ttl else "",
                text=normalize(bdy.get_text(" ", strip=True)),
            )
        )
    return out


def parse_act(html: str) -> list[Articol]:
    """Sparge pagina unui act in articole structurate, cu ierarhie si note."""
    soup = BeautifulSoup(html, "lxml")
    articole: list[Articol] = []

    for art in soup.find_all(class_="S_ART"):
        ttl = art.find(class_="S_ART_TTL")
        if ttl is None:
            continue
        eticheta = normalize(ttl.get_text(" ", strip=True))
        m = re.search(r"(\d+(?:\^\d+)?)", eticheta)
        if not m:
            continue  # fara numar nu putem cita, deci nu il pastram

        den = art.find(class_="S_ART_DEN")
        # Notele se detaseaza intai, ca sa nu contamineze textul normativ.
        note = _extract_notes(art)
        body = art.find(class_="S_ART_BDY")
        alineate = _alineate_of(art) or _literele_of(art)

        if alineate:
            text = "\n".join(f"{a.numar} {a.text}".strip() for a in alineate)
        else:
            text = normalize(body.get_text(" ", strip=True)) if body else ""

        articole.append(
            Articol(
                numar=m.group(1),
                eticheta=eticheta,
                denumire=normalize(den.get_text(" ", strip=True)) if den else "",
                text=text,
                alineate=alineate,
                parinti=_ancestors(art),
                note=note,
                referinte=sorted(
                    {normalize(r.get_text(" ", strip=True)) for r in art.find_all(class_="S_LGI")}
                    - {""}
                ),
            )
        )

    return articole


def to_dicts(articole: list[Articol]) -> list[dict]:
    return [asdict(a) | {"cale": a.cale()} for a in articole]
