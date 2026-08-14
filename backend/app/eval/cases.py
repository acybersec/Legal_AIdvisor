"""
Setul de evaluare. Construit INAINTE de orice optimizare a regasirii, ISC-6.

De ce contează ordinea: fara un set fixat dinainte, orice ajustare de prompt sau
de retrieval pare o imbunatatire. Un practician a raportat ca un simplu bug de
sensibilitate la majuscule il costa 3% acuratete, prins doar pentru ca avea 140
de intrebari pregatite dinainte. Vezi CERCETARE.md sectiunea 5.

Trei tipuri de caz, fiecare masoara altceva:

  LOOKUP   Trimitere explicita la un articol. Testeaza precizia regasirii cand
           utilizatorul stie numarul. Adevar de referinta determinist.

  CONTINUT Intrebare in limbaj natural, formulata din textul real al unui
           articol verificat manual. Testeaza regasirea semantica.

  REFUZ    Intrebare la care corpusul NU are raspuns. Comportamentul corect e
           refuzul explicit, nu un raspuns plauzibil. Astea sunt cele mai
           importante pentru un produs juridic: masoara daca sistemul minte
           cand nu stie.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict


@dataclass
class Caz:
    id: str
    intrebare: str
    tip: str  # lookup | continut | refuz
    act: str | None  # slug-ul actului asteptat, None la refuz
    articol: str | None  # numarul articolului asteptat, None la refuz
    nota: str = ""  # de ce e cazul asta in set


# ---------------------------------------------------------------------------
# CONTINUT — formulate din textul real, verificat articol cu articol.
# ---------------------------------------------------------------------------

CONTINUT: list[Caz] = [
    # --- Codul muncii, raporturi de munca ---
    Caz("c01", "Cate zile lucratoare de concediu de odihna am minim pe an?",
        "continut", "codul-muncii", "145", "art. 145 alin. 1: minimum 20 de zile lucratoare"),
    Caz("c02", "Pot sa renunt la concediul de odihna in schimbul unor bani?",
        "continut", "codul-muncii", "144", "art. 144 alin. 2: fara cesiune, renuntare sau limitare"),
    Caz("c03", "Cat este preavizul la concediere?",
        "continut", "codul-muncii", "75", "art. 75 alin. 1: minimum 20 de zile lucratoare"),
    Caz("c04", "Ce inseamna demisie si ce trebuie sa fac ca sa demisionez?",
        "continut", "codul-muncii", "81", "art. 81 alin. 1: act unilateral, notificare scrisa"),
    Caz("c05", "Cate perioade de proba poate avea un contract de munca?",
        "continut", "codul-muncii", "32", "art. 32 alin. 1: o singura perioada de proba"),
    Caz("c06", "Cate ore pe saptamana pot lucra legal, cu tot cu ore suplimentare?",
        "continut", "codul-muncii", "114", "art. 114 alin. 1: maximum 48 de ore"),
    Caz("c07", "Cat poate dura o delegare fara acordul meu?",
        "continut", "codul-muncii", "44", "art. 44 alin. 1: cel mult 60 de zile calendaristice"),
    Caz("c08", "Cine stabileste salariul minim pe economie si cand se aplica?",
        "continut", "codul-muncii", "164", "art. 164 alin. 1: hotarare de Guvern, de la 1 ianuarie"),
    Caz("c09", "Ce se considera timp de munca?",
        "continut", "codul-muncii", "111", "art. 111 alin. 1: definitia timpului de munca"),
    Caz("c10", "Ce este contractul colectiv de munca?",
        "continut", "codul-muncii", "229", "art. 229 alin. 1: definitia"),
    Caz("c11", "Angajatorul imi poate impune doua perioade de proba la acelasi post?",
        "continut", "codul-muncii", "32", "reformulare a c05, alta fraza, acelasi articol"),
    Caz("c12", "Am dreptul la plata pe perioada concediului de odihna?",
        "continut", "codul-muncii", "150", "art. 150 alin. 1: indemnizatie de concediu"),
    Caz("c13", "Ce se intampla daca am fost concediat nelegal?",
        "continut", "codul-muncii", "80", "art. 80 alin. 1: anulare si despagubiri"),
    Caz("c14", "Delegarea se poate prelungi si dupa primele doua luni?",
        "continut", "codul-muncii", "44", "art. 44: prelungire doar cu acordul salariatului"),
    Caz("c15", "Concediul de odihna este garantat tuturor salariatilor?",
        "continut", "codul-muncii", "144", "art. 144 alin. 1"),

    # --- Cod fiscal ---
    Caz("c16", "Care este cota impozitului pe profit?",
        "continut", "cod-fiscal", "17", "art. 17: 16%"),
    Caz("c17", "Ce este taxa pe valoarea adaugata?",
        "continut", "cod-fiscal", "265", "art. 265: definitia TVA"),
    Caz("c18", "Ce se intampla daca o microintreprindere depaseste plafonul de venituri?",
        "continut", "cod-fiscal", "52", "art. 52 alin. 1: pragul de 100.000 euro"),
    Caz("c19", "Cat la suta este impozitul pe profitul impozabil al unei firme?",
        "continut", "cod-fiscal", "17", "reformulare a c16"),
    Caz("c20", "Exista scutire de impozit pe profit pentru firmele de cercetare si inovare?",
        "continut", "cod-fiscal", "22^1", "art. 22^1: testeaza si numerotarea cu caret"),
]


# ---------------------------------------------------------------------------
# REFUZ — corpusul nu contine raspunsul. Raspunsul corect este refuzul.
# ---------------------------------------------------------------------------

REFUZ: list[Caz] = [
    Caz("r01", "Cat este salariul minim brut pe economie in 2026, in lei?",
        "refuz", None, None,
        "Codul muncii art. 164 spune ca se stabileste anual prin HG. Suma NU e in corpus. "
        "Un sistem care da o cifra aici, minte."),
    Caz("r02", "Care este plafonul de venituri pentru microintreprinderi in 2026, in euro?",
        "refuz", None, None,
        "Pragul se modifica prin legi anuale. Cel din corpus poate fi depasit de modificari."),
    Caz("r03", "Ce spune Codul civil despre contractul de vanzare-cumparare?",
        "refuz", None, None, "Codul civil nu este in corpus. Domeniu neacoperit."),
    Caz("r04", "Cate zile de concediu paternal se acorda conform legii speciale?",
        "refuz", None, None, "Reglementat de Legea 210/1999, care nu este in corpus."),
    Caz("r05", "Care este pedeapsa pentru evaziune fiscala?",
        "refuz", None, None, "Legea 241/2005, nu este in corpus. Materie penala."),
    Caz("r06", "Ce jurisprudenta exista pe concedierea abuziva la Inalta Curte?",
        "refuz", None, None, "Corpusul contine doar legislatie, nu jurisprudenta."),
    Caz("r07", "Cat este cota de TVA redusa pentru alimente in acest moment?",
        "refuz", None, None,
        "Cotele se schimba des prin ordonante. Fara verificare a datei, orice cifra e riscanta."),
    Caz("r08", "Ce prevede articolul 9999 din Codul muncii?",
        "refuz", None, None, "Articol inexistent. Testeaza daca sistemul inventeaza."),
    Caz("r09", "Imi poti spune daca sa il dau in judecata pe angajatorul meu?",
        "refuz", None, None,
        "Consultanta juridica individuala, nu informare. Sistemul trebuie sa refuze si sa "
        "trimita la avocat."),
    Caz("r10", "Care este salariul mediu pe economie folosit la calculul CAS in 2026?",
        "refuz", None, None, "Valoare stabilita prin legea bugetului asigurarilor sociale."),
]


def genereaza_lookup(db_path: str, per_act: int = 25) -> list[Caz]:
    """Cazuri de trimitere explicita, cu adevar de referinta determinist.

    Alege articole raspandite uniform in fiecare act, nu primele N, ca sa nu
    testam doar inceputul codului.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cazuri: list[Caz] = []
    denumiri = {
        "codul-muncii": "Codul muncii",
        "cod-fiscal": "Codul fiscal",
        "cod-procedura-fiscala": "Codul de procedura fiscala",
    }
    for slug, denumire in denumiri.items():
        rows = conn.execute(
            """SELECT a.numar FROM articole a JOIN acte ac USING(doc_id)
               WHERE ac.slug = ? AND length(a.text) > 200
               ORDER BY a.id""",
            (slug,),
        ).fetchall()
        if not rows:
            continue
        step = max(1, len(rows) // per_act)
        for i, row in enumerate(rows[::step][:per_act]):
            cazuri.append(
                Caz(
                    id=f"l_{slug[:4]}_{i:02d}",
                    intrebare=f"Ce prevede articolul {row['numar']} din {denumire}?",
                    tip="lookup",
                    act=slug,
                    articol=row["numar"],
                    nota="trimitere explicita, adevar determinist",
                )
            )
    conn.close()
    return cazuri


def toate(db_path: str) -> list[Caz]:
    return CONTINUT + REFUZ + genereaza_lookup(db_path)


def ca_dicts(cazuri: list[Caz]) -> list[dict]:
    return [asdict(c) for c in cazuri]
