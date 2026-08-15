"""
Registrul joburilor de analiza de document. Face analiza asincrona.

PROBLEMA pe care o rezolva, si e un blocaj de vanzare, nu de eleganta:

  Analiza sincrona tinea o cerere HTTP deschisa cat dura tot documentul.
  Fiecare clauza costa un apel de generare plus doua de verificare, adica
  aproximativ 17 secunde, iar implicit se analizeaza sase clauze. Doua minute
  cu o conexiune deschisa si zero semne de viata.

  Ce se strica in practica: proxy-uri si load balancere taie conexiunile
  inactive, de obicei intre 30 si 60 de secunde, deci in orice desfasurare
  reala analiza ar esua fara ca produsul sa fie de vina. Utilizatorul nu poate
  inchide fila. Si nu vede nimic pana la sfarsit, deci nu poate distinge o
  analiza lenta de una blocata - exact problema pe care am avut-o si noi la
  evaluari, si care ne-a costat ore.

SOLUTIA: incarcarea raspunde imediat cu un identificator, iar rezultatele se
citesc la interogare, clauza cu clauza pe masura ce sunt gata.

DE CE PROGRESUL E PE CLAUZA, nu doar "gata / nu e gata": clientul vede prima
clauza analizata dupa ~17 secunde in loc de doua minute. Aceeasi durata totala,
alta experienta, si diferenta dintre "merge" si "s-a blocat" devine vizibila.

CE NU FACE, si e o alegere pentru MVP, nu o omisiune:

  Registrul e IN MEMORIE. O repornire a serverului pierde joburile in curs.
  Pentru un MVP local rulat de o persoana e alegerea corecta: o coada reala,
  Redis sau Postgres, ar adauga o dependinta de infrastructura pentru un
  castig zero la scara asta. Interfata e insa aceeasi pe care ar avea-o o
  coada adevarata, deci inlocuirea nu atinge nici API-ul, nici frontend-ul.

  Executia e cu UN SINGUR fir, deliberat. Ollama serializeaza oricum cererile
  catre acelasi model, deci mai multe fire ar produce doar cereri care asteapta
  in coada pana depasesc timeout-ul HTTP. Masurat pe evaluare: cu trei fire, 3
  din primele 4 cazuri au esuat cu eroare de retea, desi acelasi caz rulat
  izolat trecea. Documentele concurente asteapta, si asta e onest.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

# Cate joburi terminate pastram. Peste asta, cele mai vechi se arunca.
#
# Fara plafon, registrul creste cat tine procesul: fiecare raport tine textul
# clauzelor si sursele citate, deci un serviciu lasat pornit o saptamana ar
# ajunge sa consume memorie pentru rapoarte pe care nu le mai cere nimeni.
MAX_JOBURI = 50

# Termenul limita al unui job, dupa care il declaram esuat.
#
# NU e acelasi lucru cu timeout-ul HTTP, si diferenta ne-a costat o depanare.
# Timeout-ul de citire din httpx e per OPERATIE de citire, nu un termen limita:
# daca serverul din amonte trimite ceva din cand in cand, sau tine conexiunea
# deschisa fara sa raspunda, cronometrul se reseteaza si apelul nu expira
# niciodata. Masurat: cu Ollama blocat, o conexiune a stat deschisa peste opt
# minute cu timeout de citire de 180 de secunde, iar jobul a ramas "in lucru".
#
# Sase clauze x aproximativ 17 secunde inseamna sub doua minute in mod normal,
# deci zece minute e larg pentru orice document real si strans pentru un blocaj.
TERMEN_JOB_S = float(os.environ.get("TERMEN_ANALIZA_S", "600"))

IN_LUCRU = "in_lucru"
GATA = "gata"
ESUAT = "esuat"


@dataclass
class Job:
    id: str
    nume_fisier: str
    caractere: int
    clauze_total: int
    stare: str = IN_LUCRU
    # Rezultatele se adauga pe masura ce fiecare clauza se termina, ca
    # interogarea de stare sa poata arata progres real, nu doar un procent.
    rezultate: list[Any] = field(default_factory=list)
    eroare: str = ""

    @property
    def clauze_gata(self) -> int:
        return len(self.rezultate)


class Registru:
    """Joburi in memorie, cu executie pe un singur fir.

    Toate citirile si scrierile trec prin acelasi lacat. Sunt operatii de
    ordinul microsecundelor peste un dictionar, iar lucrul greu - apelurile de
    model - se face in afara lacatului, in firul de executie.
    """

    def __init__(self, max_joburi: int = MAX_JOBURI, termen_s: float = TERMEN_JOB_S):
        self._joburi: OrderedDict[str, Job] = OrderedDict()
        self._lacat = threading.Lock()
        self._max = max_joburi
        self._termen = termen_s
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analiza")
        self._paznici: dict[str, threading.Timer] = {}

    def creeaza(self, nume_fisier: str, caractere: int, clauze_total: int) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], nume_fisier=nume_fisier,
                  caractere=caractere, clauze_total=clauze_total)
        with self._lacat:
            self._joburi[job.id] = job
            self._curata()
        return job

    def _curata(self) -> None:
        """Arunca cele mai vechi joburi TERMINATE. Se apeleaza sub lacat.

        Un job in lucru nu se arunca niciodata, oricat de plin ar fi registrul:
        ar lasa un fir sa scrie intr-un job pe care nimeni nu il mai poate citi,
        iar clientul care interogheaza ar primi 404 pentru o analiza care chiar
        ruleaza.
        """
        while len(self._joburi) > self._max:
            for jid, j in self._joburi.items():
                if j.stare != IN_LUCRU:
                    del self._joburi[jid]
                    break
            else:
                return  # toate sunt in lucru; nu avem ce arunca

    def ia(self, job_id: str) -> Job | None:
        with self._lacat:
            return self._joburi.get(job_id)

    def adauga_rezultat(self, job_id: str, rezultat: Any) -> None:
        with self._lacat:
            job = self._joburi.get(job_id)
            if job is not None:
                job.rezultate.append(rezultat)

    def incheie(self, job_id: str, eroare: str = "") -> None:
        with self._lacat:
            job = self._joburi.get(job_id)
            if job is not None and job.stare == IN_LUCRU:
                job.stare = ESUAT if eroare else GATA
                job.eroare = eroare
            paznic = self._paznici.pop(job_id, None)
        if paznic is not None:
            paznic.cancel()

    def porneste(self, job_id: str, lucru: Callable[[], None]) -> None:
        """Trimite lucrul in firul de executie, cu esecul prins si pastrat.

        O exceptie scapata aici ar muri tacut in ThreadPoolExecutor, iar jobul
        ar ramane vesnic "in lucru": clientul ar interoga la nesfarsit o analiza
        care nu mai are cine sa o duca. De aceea orice esec se scrie in job.
        """
        def _ruleaza() -> None:
            try:
                lucru()
            except Exception as exc:  # noqa: BLE001 - orice esec devine stare vizibila
                self.incheie(job_id, f"{type(exc).__name__}: {exc}"[:300])
            else:
                job = self.ia(job_id)
                if job is not None and job.stare == IN_LUCRU:
                    self.incheie(job_id)

        # Paznicul de termen. Python nu poate intrerupe un fir blocat, deci nu
        # oprim lucrul; il declaram esuat, ca cel care intreaba sa afle adevarul
        # in loc sa astepte la nesfarsit un job care nu mai vine.
        #
        # LIMITARE ONESTA: firul blocat ramane ocupat, iar executorul are un
        # singur lucrator, deci analizele urmatoare asteapta dupa el. Repararea
        # completa cere izolare in alt proces, adica o coada adevarata, si e
        # dincolo de ce merita un MVP local. Pana atunci, o repornire a
        # serverului elibereaza lucratorul.
        paznic = threading.Timer(
            self._termen,
            lambda: self.incheie(
                job_id,
                f"Analiza a depasit {int(self._termen)} secunde. Serverul de inferenta "
                f"nu a raspuns. Rezultatele de mai jos sunt cele obtinute pana acum.",
            ),
        )
        paznic.daemon = True
        with self._lacat:
            self._paznici[job_id] = paznic
        paznic.start()
        self._pool.submit(_ruleaza)
