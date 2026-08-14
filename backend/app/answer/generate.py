"""
Generarea raspunsului, cu citari atasate programatic. ISC-2 si ISC-3.

Contractul de citare, si de ce e impus in cod si nu prin prompt:

  Modelul primeste extrasele etichetate [S1], [S2] si are voie sa scrie DOAR
  aceste marcaje. Sirul de citare afisat utilizatorului este inlocuit ulterior
  din coloana `citare` a bazei de date, compusa la ingestie.

  Instructiunea din prompt NU este suficienta. Masurat pe llama3.1:8b: cerut
  explicit sa nu scrie numarul articolului, modelul a raspuns totusi "Conform
  articolului 145, ...". Un produs juridic care se bazeaza pe bunavointa
  modelului pentru corectitudinea citarilor va publica citari inventate.

  De aceea exista `detecteaza_citari_inventate`: o trimitere juridica scrisa de
  model si ABSENTA din sursele primite este o INCALCARE si opreste raspunsul.
  Nu o curatam tacut, pentru ca stergerea ar ascunde faptul ca modelul a
  inventat. Trimiterile care exista in textul surselor sunt citate fidele, nu
  inventii, si trec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from ..search.retrieve import Rezultat

OLLAMA = "http://10.0.1.123:11434"
MODEL_GENERARE = "llama3.1:8b"

# Trimiteri juridice scrise de model.
#
# Formele flexionare conteaza si aici. Prima versiune acoperea "articolul 145"
# dar rata "conform articolului 145", care este exact formularea pe care
# llama3.1 a produs-o spontan in primul test. O garda care rateaza cea mai
# frecventa formulare romaneasca nu e o garda.
#
# Acoperim: art., articol, articolul, articolului, articole, articolele,
# articolelor, alin., alineat, alineatul, alineatului, plus trimiteri la legi.
_CITARE_INVENTATA = re.compile(
    r"\b(?:art\.?|articol(?:ul(?:ui)?|e(?:le|lor)?)?"
    r"|alin\.?|alineat(?:ul(?:ui)?|e(?:le|lor)?)?)"
    r"\s*\.?\s*\d"
    r"|\b(?:legea|legii|codul|codului)\s+(?:nr\.?\s*)?\d"
    r"|\bnr\.?\s*\d+\s*/\s*\d{4}",
    re.IGNORECASE,
)

_SLOT = re.compile(r"\[S(\d+)\]")

SEMNAL_INSUFICIENT = "INSUFICIENT"

PROMPT = """Esti asistent juridic pentru firme din Romania. Raspunzi in limba romana, clar si la obiect, in 2-5 propozitii.

REGULI:
- Foloseste DOAR informatia din extrasele de mai jos.
- Dupa fiecare afirmatie pune marcajul sursei, exact in forma [S1] sau [S2].
- NU scrie numele legii, numarul articolului sau cuvintele articol si alineat. Doar marcajul.
- Daca extrasele nu contin raspunsul la intrebare, scrie un singur cuvant: {semnal}
- Nu da sfaturi juridice individuale. Prezinta ce prevede legea.

EXTRASE:
{extrase}

INTREBARE: {intrebare}

RASPUNS:"""


@dataclass
class Raspuns:
    text: str  # textul cu citari reale substituite
    text_brut: str  # ce a produs modelul, cu marcaje
    surse: list[Rezultat] = field(default_factory=list)  # doar cele efectiv citate
    insuficient: bool = False
    incalcari: list[str] = field(default_factory=list)  # citari inventate detectate

    @property
    def valid(self) -> bool:
        return not self.insuficient and not self.incalcari and bool(self.surse)


def construieste_extrase(rezultate: list[Rezultat], max_chars: int = 1800) -> str:
    blocuri = []
    for i, r in enumerate(rezultate, start=1):
        corp = r.text[:max_chars]
        blocuri.append(f"[S{i}] {r.cale}\n{corp}")
    return "\n\n".join(blocuri)


def _normalizeaza_trimitere(t: str) -> str:
    """Forma canonica a unei trimiteri, ca sa comparam 'art. 61' cu 'art.61'."""
    return re.sub(r"[\s.]+", "", t).lower()


def detecteaza_citari_inventate(text: str, surse: list[Rezultat]) -> list[str]:
    """Trimiteri juridice scrise de model care NU exista in sursele date.

    Nuanta care conteaza: textele de lege sunt pline de trimiteri interne, de
    tipul "concediat in temeiul art. 61 lit. c)". Cand modelul reda corect un
    articol, reproduce si aceste trimiteri. Ele NU sunt inventii.

    Fara filtrul asta, garda ISC-3 respingea raspunsuri corecte pentru ca
    citasera fidel legea. Masurat: 2 din 12 cazuri, respinse pe nedrept.

    Deci semnalam doar trimiterile absente din textul surselor. Aceea e
    definitia utila a inventiei: modelul a scris o referinta pe care nimeni nu
    i-a dat-o.
    """
    corp_surse = _normalizeaza_trimitere(" ".join(s.text for s in surse))
    inventate = []
    for m in _CITARE_INVENTATA.finditer(text):
        bucata = m.group(0).strip()
        if _normalizeaza_trimitere(bucata) not in corp_surse:
            inventate.append(bucata)
    return inventate


def leaga_citari(text: str, rezultate: list[Rezultat]) -> tuple[str, list[Rezultat]]:
    """Inlocuieste [Sn] cu sirul de citare stocat. Intoarce si sursele folosite."""
    folosite: dict[int, Rezultat] = {}

    def _sub(m: re.Match) -> str:
        idx = int(m.group(1))
        if 1 <= idx <= len(rezultate):
            r = rezultate[idx - 1]
            folosite[idx] = r
            return f"({r.citare})"
        return ""  # marcaj catre o sursa inexistenta: il eliminam

    legat = _SLOT.sub(_sub, text)
    legat = re.sub(r"\s+([.,;])", r"\1", legat)
    return legat.strip(), [folosite[i] for i in sorted(folosite)]


def genereaza(intrebare: str, rezultate: list[Rezultat], *,
              model: str = MODEL_GENERARE, temperatura: float = 0.2,
              timeout: float = 240.0) -> Raspuns:
    if not rezultate:
        return Raspuns(text="", text_brut="", insuficient=True)

    prompt = PROMPT.format(
        semnal=SEMNAL_INSUFICIENT,
        extrase=construieste_extrase(rezultate),
        intrebare=intrebare,
    )
    r = httpx.post(
        f"{OLLAMA}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False,
              "options": {"temperature": temperatura, "num_predict": 500}},
        timeout=timeout,
    )
    r.raise_for_status()
    brut = r.json()["response"].strip()

    if SEMNAL_INSUFICIENT in brut.upper():
        return Raspuns(text="", text_brut=brut, insuficient=True)

    incalcari = detecteaza_citari_inventate(brut, rezultate)
    legat, folosite = leaga_citari(brut, rezultate)
    return Raspuns(text=legat, text_brut=brut, surse=folosite, incalcari=incalcari)
