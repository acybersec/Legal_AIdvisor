"""
Reordonarea candidatilor regasiti, cu un model. Al doilea pas al regasirii.

PROBLEMA, masurata, nu presupusa:

  Intrebarea "Ce se considera timp de munca?" scotea art. 111 pe locul OPT din
  opt. Articolul 111 spune, textual, "Timpul de munca reprezinta orice perioada
  in care salariatul presteaza munca". Deasupra lui stateau art. 113
  (repartizarea timpului de munca) si art. 130 (norma de munca).

  Cauza nu e un defect de implementare, ci felul in care functioneaza BM25:
  scorul creste cu frecventa termenului. Un articol care FOLOSESTE repetat
  "timpul de munca" bate articolul care il DEFINESTE o singura data si trece
  mai departe. Pentru un corpus juridic asta e sistematic, fiindca articolele
  de definitii sunt scurte si enunta termenul o data.

  Regasirea vectoriala nu repara singura problema: art. 113 si 111 sunt
  semantic apropiate, ambele vorbesc despre acelasi subiect. Diferenta dintre
  "vorbeste despre X" si "raspunde la intrebarea despre X" cere citirea
  ambelor texte in raport cu intrebarea, adica exact ce nu poate face nici un
  scor calculat pe cuvinte, si nici o singura reprezentare vectoriala.

SOLUTIA: un pas de reordonare listwise. Modelul primeste intrebarea si toti
candidatii deodata si intoarce ordinea. Listwise, nu punctaj per candidat, din
doua motive: un singur apel in loc de N, si modelul poate COMPARA candidatii
intre ei, ceea ce e chiar intrebarea la care raspundem.

DE CE EXISTA SI O POARTA, nu doar o reordonare:

  Prima versiune a acestui modul doar reordona. Regasirea a urcat de la 85% la
  100% recall@1 - si rezultatul CAP LA CAP s-a inrautatit. Raspunsurile false
  au crescut de la 2 la 4, iar din cele 10 intrebari la care sistemul TREBUIE
  sa refuze, a inceput sa raspunda la 3.

  Cauza e o proprietate a oricarui reranker, nu un defect al acestuia: el
  produce INTOTDEAUNA un "cel mai bun", chiar cand niciun candidat nu e bun.
  Transforma "nu exista potrivire" in "uite cea mai buna dintr-un lot prost".

  Concret: la "Cat este cota de TVA redusa pentru alimente IN ACEST AN",
  reordonarea punea art. 291, Cotele, pe primul loc, cu incredere. Articolul
  chiar vorbeste despre cote reduse, dar corpusul e o fotografie si nu stie
  anul curent. Generatorul primea un articol curat, la subiect, si raspundea
  "11%". Fara reordonare, primele cinci rezultate erau amestecate si
  generatorul semnala singur ca nu are raspunsul - adica dezordinea facea,
  accidental, munca unei porti.

  De aceea reordonarea e urmata de o POARTA: un al doilea apel care raspunde
  la o singura intrebare, "contin extrasele informatia ceruta?". Lista goala
  inseamna, mai departe in lant, refuz explicit.

  Lectia generala, si merita retinuta dincolo de proiectul asta: masoara
  intotdeauna la capatul lantului. O metrica intermediara care urca poate
  ascunde o metrica finala care coboara.

CONTRACTUL DE SIGURANTA, partea importanta:

  Reordonarea nu poate face rezultatul mai prost decat regasirea de baza.
  Orice iesire pe care nu o intelegem - JSON invalid, indici in afara
  intervalului, model cazut, timeout - inseamna ca pastram ordinea primita.
  Un candidat pe care modelul nu il mentioneaza NU se pierde: se adauga la
  coada, in ordinea initiala.

  Motivul e ca reordonarea e o imbunatatire optionala peste un lant care
  functioneaza deja. Daca ar putea sa strice, ar trebui masurata cu totul
  altfel; asa, cel mai rau caz este ca nu ajuta.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from .retrieve import Rezultat, cere_valoare_la_zi  # noqa: F401

OLLAMA = os.environ.get("OLLAMA_URL", "http://10.0.1.123:11434")

# Modelul de reordonare. E judecata, nu fluenta: trebuie sa distinga intre un
# articol care vorbeste despre un subiect si cel care raspunde la intrebare.
# Acolo un model mic se vede imediat, deci implicit e cel puternic.
MODEL_RERANK = os.environ.get("MODEL_RERANK", "qwen3:14b")

# Comutator de mediu. MODEL_RERANK="" opreste pasul complet, fara atingerea
# codului, si lantul se intoarce la comportamentul de dinainte. Util ca sa
# masori exact cat aduce reordonarea.
ACTIV = bool(MODEL_RERANK.strip())

# Poarta de acoperire, al doilea apel. Se opreste cu POARTA_RERANK=0, ca sa se
# poata masura separat cat aduce fiecare din cele doua jumatati.
POARTA_ACTIVA = os.environ.get("POARTA_RERANK", "1").strip() not in ("", "0")

# Cat text vede fiecare din cele doua apeluri. Bugetele DIFERA, si diferenta
# nu e o optimizare, e o corectura de defect.
#
# REORDONAREA raspunde la "despre ce e articolul asta", iar subiectul se
# stabileste din primele randuri. 450 de caractere x 8 candidati tine promptul
# mic acolo unde conteaza.
#
# POARTA raspunde la "se afla raspunsul in text", ceea ce e cu totul altceva:
# nu poate confirma o informatie pe care nu o vede. Masurat: la "Cat este
# preavizul la demisie", art. 81 statea corect pe primul loc, dar durata -
# "20 de zile lucratoare" - se afla la caracterul 641. Poarta vedea 450 si
# raspundea "nu specifica durata preavizului". Avea dreptate despre ce i se
# aratase; trunchierea era defectul, si producea un refuz fals pe un raspuns
# perfect valid.
#
# Poarta primeste deci acelasi buget ca generatorul, si asta e regula: poarta
# judeca EXACT textul pe care generatorul il va folosi. Orice alta valoare ar
# insemna ca decidem pe alte probe decat cele pe care se scrie raspunsul.
_MAX_CHARS = 450
_MAX_CHARS_POARTA = 1800  # tinut in pas cu construieste_extrase din answer/generate.py

# Doua sarcini, DOUA apeluri, si asta e o decizie masurata.
#
# Prima versiune le cerea impreuna: "spune daca exista raspuns SI pune-le in
# ordine". Modelul le facea pe amandoua prost - intorcea "exista_raspuns": true
# la orice si ordinea [1,2,3,4,5,6,7,8], adica permutarea identica, adica nu
# reordona deloc. Separate, poarta a dat 7 din 8 pe un esantion tinta.
#
# Costul e un apel in plus per intrebare. Merita platit doar daca reduce
# raspunsurile false masurate cap la cap; altfel amandoua se scot.
PROMPT = """Esti un bibliotecar juridic. Primesti o intrebare si mai multe articole de lege gasite automat. Le pui in ordinea in care RASPUND la intrebare.

Criteriul, si singurul care conteaza:
- Primul trebuie sa fie articolul care raspunde DIRECT la intrebare.
- Un articol care DEFINESTE termenul din intrebare bate un articol care doar foloseste acel termen.
- Un articol care detaliaza un caz particular sta dupa cel care da regula generala.

Nu explica nimic. Nu inventa numere care nu sunt in lista.

INTREBARE: {intrebare}

ARTICOLE:
{candidati}

Raspunde DOAR cu JSON, cu numerele articolelor din lista, cel mai relevant primul:
{{"ordine": [numar, numar, ...]}}"""

PROMPT_POARTA = """Ai o intrebare si extrase din legislatie. Raspunzi la o singura intrebare: extrasele contin informatia ceruta, da sau nu?

Pui "acoperit": false in aceste situatii:
- intrebarea cere valoarea in vigoare ACUM ("in acest an", "in prezent", "actuala") - extrasele sunt un text de lege fara data curenta, deci NU pot confirma ce e valabil azi
- intrebarea cere o recomandare personala ("ce sa fac", "sa il dau in judecata", "ma sfatuiesti") - legea nu da sfaturi
- extrasele sunt din acelasi domeniu dar despre altceva decat s-a intrebat

Pui "acoperit": true doar cand un extras chiar contine raspunsul.

INTREBARE: {intrebare}

EXTRASE:
{candidati}

Raspunde DOAR cu JSON: {{"acoperit": true sau false, "de_ce": "cinci cuvinte"}}"""


def _bloc_candidati(candidati: list[Rezultat], max_chars: int = _MAX_CHARS) -> str:
    return "\n\n".join(
        f"[{i}] {c.cale}\n{c.text[:max_chars]}"
        for i, c in enumerate(candidati, start=1)
    )


def _cere_ordine(prompt: str, model: str, timeout: float) -> list[int] | None:
    """Indicii intorsi de model, sau None daca iesirea nu se poate citi."""
    try:
        r = httpx.post(
            f"{OLLAMA}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json",
                  # Vezi nota din answer/verify.py: modelele cu mod de gandire
                  # isi consuma tot bugetul pe rationament si intorc gol.
                  "think": False,
                  "keep_alive": "60m",
                  "options": {"temperature": 0.0, "num_predict": 200, "num_ctx": 8192}},
            timeout=timeout,
        )
        r.raise_for_status()
        brut = r.json()["response"].strip()
    except Exception:
        # Reteaua sau modelul au cazut. Nu e un motiv sa cada si raspunsul:
        # apelantul pastreaza ordinea de baza.
        return None

    try:
        d = json.loads(brut)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", brut, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    ordine = d.get("ordine")
    if not isinstance(ordine, list):
        return None
    return [x for x in ordine if isinstance(x, int)]


def _acoperit(intrebare: str, candidati: list[Rezultat], model: str,
              timeout: float) -> bool:
    """Contin extrasele raspunsul la intrebare?

    La orice esec intoarce True, adica lasa raspunsul sa treaca. Poarta e o
    aparare in plus peste cei doi verificatori de dupa generare; daca ea cade,
    lantul trebuie sa se comporte ca inainte de a exista, nu sa refuze tot.
    """
    # Verificarea determinista intai: e gratuita si nu poate regresa.
    if cere_valoare_la_zi(intrebare):
        return False

    prompt = PROMPT_POARTA.format(
        intrebare=intrebare,
        candidati=_bloc_candidati(candidati, _MAX_CHARS_POARTA),
    )
    try:
        r = httpx.post(
            f"{OLLAMA}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json",
                  "think": False, "keep_alive": "60m",
                  "options": {"temperature": 0.0, "num_predict": 120, "num_ctx": 8192}},
            timeout=timeout,
        )
        r.raise_for_status()
        d = json.loads(r.json()["response"].strip())
    except Exception:
        return True
    # Acoperirea se INFIRMA explicit. Un camp lipsa nu inchide poarta.
    return d.get("acoperit") is not False


def rerankeaza(intrebare: str, candidati: list[Rezultat], *, k: int = 5,
               model: str = MODEL_RERANK, timeout: float = 120.0) -> list[Rezultat]:
    """Candidatii reordonati dupa cat de bine raspund la intrebare.

    Intoarce cel mult `k` rezultate. La orice esec, intoarce primii `k`
    candidati in ordinea primita - vezi contractul de siguranta din antet.
    """
    if not ACTIV or len(candidati) < 2:
        return candidati[:k]

    ordine = _cere_ordine(
        PROMPT.format(intrebare=intrebare, candidati=_bloc_candidati(candidati)),
        model, timeout,
    )
    if not ordine:
        return candidati[:k]

    # Indicii din prompt sunt 1..n. Tot ce iese din interval sau se repeta se
    # ignora tacut: un model care halucineaza indici nu trebuie sa poata scoate
    # din lista un candidat legitim.
    vazute: set[int] = set()
    reordonat: list[Rezultat] = []
    for idx in ordine:
        if 1 <= idx <= len(candidati) and idx not in vazute:
            vazute.add(idx)
            reordonat.append(candidati[idx - 1])

    # Candidatii pe care modelul nu i-a mentionat se pastreaza, la coada, in
    # ordinea initiala. Reordonarea schimba prioritatea, nu compozitia.
    for i, c in enumerate(candidati, start=1):
        if i not in vazute:
            reordonat.append(c)

    final = reordonat[:k]

    # Poarta de acoperire. Lista goala inseamna, mai jos in lant, "extrasele nu
    # contin raspunsul", deci refuz explicit. Ruleaza DUPA reordonare, ca sa
    # judece cei mai buni candidati, nu pe cei pe care ii scosese RRF in fata.
    if POARTA_ACTIVA and final and not _acoperit(intrebare, final, model, timeout):
        return []
    return final
