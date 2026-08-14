"""
Lantul complet: regasire, generare, doua verificari, decizie.

Ordinea portilor conteaza. Sunt asezate de la cea mai ieftina la cea mai
scumpa, si oricare cade opreste lantul:

  1. Regasire goala            -> refuz, fara apel la model
  2. Model spune INSUFICIENT   -> refuz
  3. Citari inventate de model -> refuz, incalcare ISC-3
  4. Verificator ancorare      -> refuz daca gaseste afirmatii nesustinute
  5. Verificator infirmare     -> refuz daca reuseste sa infirme

Refuzul nu e o eroare de sistem. Pentru un consilier juridic, "nu pot verifica"
este raspunsul corect cand nu exista temei, si e preferabil unui raspuns
increzator si gresit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..search.retrieve import Retriever, Rezultat
from .generate import Raspuns, genereaza
from .verify import Verdict, verifica_tot

MESAJ_REFUZ = (
    "Nu pot raspunde la aceasta intrebare pe baza legislatiei pe care o am indexata. "
    "Nu iti dau un raspuns pe care nu il pot proba. Verifica direct in Monitorul Oficial "
    "sau intreaba un avocat."
)


@dataclass
class RezultatFinal:
    intrebare: str
    raspuns: str  # textul livrat, sau mesajul de refuz
    a_refuzat: bool
    motiv_refuz: str = ""
    surse: list[Rezultat] = field(default_factory=list)
    verdicte: list[Verdict] = field(default_factory=list)
    candidati: list[Rezultat] = field(default_factory=list)


class Pipeline:
    def __init__(self, db_path: str | Path, index_path: str | Path, *,
                 k: int = 4, model_v2: str | None = None):
        self.retriever = Retriever(db_path, index_path)
        self.k = k
        self.model_v2 = model_v2

    def raspunde(self, intrebare: str) -> RezultatFinal:
        hits = self.retriever.cauta(intrebare, k=self.k)
        if not hits:
            return RezultatFinal(intrebare, MESAJ_REFUZ, True,
                                 "regasirea nu a intors niciun articol")

        gen: Raspuns = genereaza(intrebare, hits)

        if gen.insuficient:
            return RezultatFinal(intrebare, MESAJ_REFUZ, True,
                                 "modelul a semnalat ca extrasele nu contin raspunsul",
                                 candidati=hits)

        if gen.incalcari:
            return RezultatFinal(
                intrebare, MESAJ_REFUZ, True,
                f"modelul a scris citari proprii, incalcare ISC-3: {gen.incalcari[:3]}",
                candidati=hits,
            )

        if not gen.surse:
            return RezultatFinal(intrebare, MESAJ_REFUZ, True,
                                 "raspunsul nu a citat nicio sursa", candidati=hits)

        # Verificatorii primesc textul BRUT, cu marcajele [Sn] intacte. Vezi
        # nota din verify._extrase: textul cu citari legate ii facea sa respinga
        # raspunsuri corecte, judecand datele actelor drept afirmatii de fond.
        verdicte = verifica_tot(
            intrebare, gen.text_brut, gen.surse,
            **({"model_v2": self.model_v2} if self.model_v2 else {}),
        )
        cazute = [v for v in verdicte if not v.trecut]
        if cazute:
            return RezultatFinal(
                intrebare, MESAJ_REFUZ, True,
                "; ".join(f"{v.nume}: {v.motiv}" for v in cazute),
                gen.surse, verdicte, hits,
            )

        return RezultatFinal(intrebare, gen.text, False, "", gen.surse, verdicte, hits)
