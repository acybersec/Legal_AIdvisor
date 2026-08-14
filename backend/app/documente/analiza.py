"""
Analiza unui document incarcat, prin ACELASI lant de verificare ca la intrebari.

Cerinta ISC-5 este explicita: fluxul de document ruleaza aceleasi verificari.
De aceea aici nu exista un al doilea lant, ci o reformulare a fiecarei clauze
intr-o intrebare, trecuta prin `Pipeline.raspunde`. Daca lantul refuza, clauza
apare ca neacoperita, nu ca fara probleme.

Distinctia care conteaza pentru un produs juridic:

    "nu am gasit temei legal aplicabil"   NU inseamna   "clauza e in regula"

Un instrument care confunda cele doua da clientului fals confort.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..answer.pipeline import Pipeline
from ..search.retrieve import Rezultat
from .extrage import Clauza


@dataclass
class ClauzaAnalizata:
    index: int
    fragment: str  # inceputul clauzei, pentru identificare in interfata
    acoperita: bool  # exista temei legal identificat si verificat
    observatie: str  # ce spune legea, sau de ce nu s-a putut stabili
    surse: list[Rezultat] = field(default_factory=list)


@dataclass
class RaportDocument:
    nume_fisier: str
    caractere: int
    clauze_analizate: int
    clauze_acoperite: int
    rezultate: list[ClauzaAnalizata] = field(default_factory=list)
    avertisment: str = (
        "Analiza acopera doar legislatia indexata: Codul muncii, Codul fiscal si "
        "Codul de procedura fiscala. O clauza marcata drept neacoperita NU este "
        "declarata conforma, ci doar nu are temei identificat in acest corpus."
    )


def _intrebare_din_clauza(clauza: Clauza) -> str:
    return (
        "Ce prevede legislatia romaneasca a muncii sau fiscala in legatura cu "
        f"urmatoarea clauza dintr-un document: {clauza.text[:600]}"
    )


def analizeaza(pipe: Pipeline, nume_fisier: str, text: str,
               clauze: list[Clauza]) -> RaportDocument:
    rezultate: list[ClauzaAnalizata] = []
    for clauza in clauze:
        rez = pipe.raspunde(_intrebare_din_clauza(clauza))
        if rez.a_refuzat:
            rezultate.append(
                ClauzaAnalizata(
                    index=clauza.index,
                    fragment=clauza.text[:180],
                    acoperita=False,
                    observatie="Nu am identificat temei legal verificabil in corpusul indexat.",
                )
            )
        else:
            rezultate.append(
                ClauzaAnalizata(
                    index=clauza.index,
                    fragment=clauza.text[:180],
                    acoperita=True,
                    observatie=rez.raspuns,
                    surse=rez.surse,
                )
            )

    return RaportDocument(
        nume_fisier=nume_fisier,
        caractere=len(text),
        clauze_analizate=len(rezultate),
        clauze_acoperite=sum(1 for r in rezultate if r.acoperita),
        rezultate=rezultate,
    )
