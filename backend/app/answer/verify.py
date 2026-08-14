"""
Doi verificatori independenti. ISC-4.

Regula produsului: daca ORICARE dintre ei cade, raspunsul NU se trimite si
sistemul refuza explicit. Refuzul e o functie, nu un esec.

De ce doi, si de ce diferiti la framing:

  V1 ANCORARE   Primeste raspunsul si sursele si cauta afirmatii nesustinute.
                Intrebarea lui e "ce NU scrie in surse".

  V2 INFIRMARE  Primeste aceleasi date, dar cu sarcina inversa: sa incerce sa
                INFIRME raspunsul, cu instructiunea explicita de a alege
                "infirmat" cand nu e sigur. Framing adversarial, ca sa nu
                repete pur si simplu judecata primului.

  Sursele ii ajung lui V2 in ordine inversa fata de V1, ca pozitia in context
  sa nu produca aceeasi eroare la amandoi.

LIMITARE ONESTA, de stiut inainte de vanzare:
  Ambii ruleaza pe acelasi model, llama3.1:8b, pentru ca pe masina locala nu
  exista altul. Doua instante ale aceluiasi model pot gresi identic, deci
  garantia e mai slaba decat suna "doi verificatori independenti". Interfata
  primeste modelul ca parametru tocmai ca al doilea sa poata fi mutat pe un
  model mai puternic, prin API, fara sa se schimbe restul lantului.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from ..search.retrieve import Rezultat

import os

OLLAMA = os.environ.get("OLLAMA_URL", "http://10.0.1.123:11434")

# Modelele de verificare, configurabile fara atingerea codului.
#
# De ce doua variabile si nu una: independenta verificatorilor creste daca
# ruleaza pe modele DIFERITE. Doua instante ale aceluiasi model gresesc corelat,
# ceea ce face din "doi verificatori" o formalitate.
#
# Verificarea e locul unde judecata conteaza cel mai mult si unde un model slab
# se vede imediat: masurat pe llama3.1:8b, respingea reformulari corecte cu
# motive de tipul "nu se regaseste in extras, dar este o traducere a acesteia".
# Generarea poate ramane pe un model mic; verificarea nu.
MODEL_IMPLICIT = os.environ.get("MODEL_VERIFICATOR", "llama3.1:8b")
MODEL_V2 = os.environ.get("MODEL_VERIFICATOR_2", MODEL_IMPLICIT)

PROMPT_ANCORARE = """Verifici daca fiecare afirmatie de fond dintr-un raspuns se regaseste in extrasele date.

Extrasele sunt numerotate [S1], [S2] si asa mai departe. In raspuns, marcajele [S1] arata din ce extras provine afirmatia.

IGNORA complet marcajele [S1], numele legilor si datele actelor. Acelea sunt generate automat si nu le verifici tu.
Verifici DOAR fondul: cifre, termene, conditii, drepturi si obligatii.

FOARTE IMPORTANT: reformularea cu alte cuvinte este PERMISA si corecta.
Nu cauti potrivire cuvant cu cuvant. Un raspuns care spune acelasi lucru in alti termeni ESTE sustinut.
Marchezi ca nesustinut DOAR daca: o cifra difera, o conditie nu exista in extras, sau raspunsul contrazice extrasul.

EXTRASE:
{extrase}

RASPUNS DE VERIFICAT:
{raspuns}

Daca fiecare afirmatie de fond se regaseste in extrasul indicat, sustinut este true.

Raspunde DOAR cu JSON:
{{"sustinut": true sau false, "afirmatii_nesustinute": ["..."], "motiv": "o propozitie"}}"""

PROMPT_INFIRMARE = """Esti un jurist sceptic. Sarcina ta este sa INCERCI SA INFIRMI raspunsul de mai jos.

Cauta: cifre gresite, conditii inventate, generalizari peste ce spun extrasele, raspuns la alta intrebare decat cea pusa.
IGNORA marcajele [S1] si numele legilor. Acelea sunt generate automat, nu le judeci tu.
Reformularea cu alte cuvinte NU este o problema. Nu infirma un raspuns doar pentru ca foloseste alti termeni decat extrasul.
Infirmi doar daca gasesti o eroare reala de fond: cifra gresita, conditie inventata, sau contradictie cu extrasul.

EXTRASE:
{extrase}

INTREBARE PUSA: {intrebare}

RASPUNS DE ATACAT:
{raspuns}

Raspunde DOAR cu JSON, fara alt text:
{{"infirmat": true sau false, "problema": "o propozitie"}}"""


@dataclass
class Verdict:
    nume: str
    trecut: bool
    motiv: str
    brut: str = ""


def _cere_json(prompt: str, model: str, timeout: float = 240.0) -> dict:
    r = httpx.post(
        f"{OLLAMA}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "format": "json",
              "options": {"temperature": 0.0, "num_predict": 400}},
        timeout=timeout,
    )
    r.raise_for_status()
    brut = r.json()["response"].strip()
    try:
        return json.loads(brut) | {"_brut": brut}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", brut, re.S)
        if m:
            try:
                return json.loads(m.group(0)) | {"_brut": brut}
            except json.JSONDecodeError:
                pass
        # Verificatorul nu a produs verdict lizibil. Nu presupunem ca a trecut.
        return {"_ilizibil": True, "_brut": brut}


def _extrase(surse: list[Rezultat], inversat: bool = False, max_chars: int = 1500) -> str:
    """Extrasele vazute de verificatori, numerotate [Sn] ca in raspunsul brut.

    Verificatorii primesc raspunsul INAINTE de legarea citarilor, adica cu
    marcajele [S1] intacte, si extrasele cu aceleasi numere. Motivul, aflat prin
    esec masurat: cand primeau textul cu citari legate, tratau "din 8 septembrie
    2015" drept afirmatie de verificat, nu o gaseau in extrase si respingeau un
    raspuns perfect corect.

    Separarea corecta a preocuparilor: corectitudinea citarii e garantata prin
    constructie, deci nu se verifica de model. Verificatorii judeca doar fondul.

    Refuzul fals e la fel de daunator ca raspunsul fals: un sistem care respinge
    raspunsuri bune nu se poate vinde.
    """
    numerotate = [(i, s) for i, s in enumerate(surse, start=1)]
    if inversat:
        numerotate = list(reversed(numerotate))
    return "\n\n".join(
        f"[S{i}] {s.cale}\n{s.text[:max_chars]}" for i, s in numerotate
    )


def verifica_ancorare(raspuns: str, surse: list[Rezultat], *,
                      model: str = MODEL_IMPLICIT) -> Verdict:
    d = _cere_json(
        PROMPT_ANCORARE.format(extrase=_extrase(surse), raspuns=raspuns), model
    )
    if d.get("_ilizibil"):
        return Verdict("ancorare", False, "verdict ilizibil, tratat ca esec", d.get("_brut", ""))
    sustinut = bool(d.get("sustinut"))
    nesustinute = d.get("afirmatii_nesustinute") or []
    # Contradictie interna: spune sustinut dar listeaza probleme. Nu trece.
    if sustinut and nesustinute:
        return Verdict("ancorare", False,
                       f"contradictie interna: {nesustinute[:2]}", d.get("_brut", ""))
    return Verdict("ancorare", sustinut, str(d.get("motiv", ""))[:200], d.get("_brut", ""))


def verifica_infirmare(intrebare: str, raspuns: str, surse: list[Rezultat], *,
                       model: str = MODEL_IMPLICIT) -> Verdict:
    d = _cere_json(
        PROMPT_INFIRMARE.format(
            extrase=_extrase(surse, inversat=True), intrebare=intrebare, raspuns=raspuns
        ),
        model,
    )
    if d.get("_ilizibil"):
        return Verdict("infirmare", False, "verdict ilizibil, tratat ca esec", d.get("_brut", ""))
    infirmat = bool(d.get("infirmat"))
    return Verdict("infirmare", not infirmat, str(d.get("problema", ""))[:200],
                   d.get("_brut", ""))


def verifica_tot(intrebare: str, raspuns: str, surse: list[Rezultat], *,
                 model_v1: str = MODEL_IMPLICIT,
                 model_v2: str = MODEL_V2) -> list[Verdict]:
    return [
        verifica_ancorare(raspuns, surse, model=model_v1),
        verifica_infirmare(intrebare, raspuns, surse, model=model_v2),
    ]
