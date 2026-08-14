"""
Evaluare cap la cap pe intreg setul. ISC-6, partea de masurare.

Metrica care conteaza cel mai mult pentru un produs juridic NU este acuratetea.
Este RATA DE RASPUNS FALS: cazuri in care sistemul a raspuns increzator, dar pe
temei gresit. Un refuz costa un client nemultumit. Un raspuns fals costa un
client care ia o decizie gresita si da vina pe tine.

De aceea rezultatele se impart in patru, nu in doua:

  CORECT      a raspuns si a citat articolul asteptat
  FALS        a raspuns dar a citat altceva          <- categoria periculoasa
  REFUZ_BUN   a refuzat, si trebuia sa refuze
  REFUZ_RAU   a refuzat, dar exista raspuns          <- categoria costisitoare
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from pathlib import Path

from app.answer.pipeline import Pipeline
from app.eval.cases import Caz, toate

ROOT = Path(__file__).resolve().parents[3]
CONCURENTA = 3  # peste asta, cele doua modele se schimba prea des in VRAM


def _clasifica(caz: Caz, rez) -> tuple[str, str]:
    if caz.tip == "refuz":
        if rez.a_refuzat:
            return "REFUZ_BUN", rez.motiv_refuz[:90]
        return "FALS", f"a raspuns cand nu trebuia: {rez.raspuns[:80]}"

    if rez.a_refuzat:
        return "REFUZ_RAU", rez.motiv_refuz[:90]

    citate = {(s.act_slug, s.numar) for s in rez.surse}
    if (caz.act, caz.articol) in citate:
        return "CORECT", ""
    return "FALS", f"a citat {sorted(citate)} in loc de {caz.act}#{caz.articol}"


def ruleaza(limita: int | None = None) -> dict:
    cazuri = toate(str(ROOT / "data" / "legal.db"))
    if limita:
        cazuri = cazuri[:limita]

    pipe = Pipeline(ROOT / "data" / "legal.db", ROOT / "data" / "vectors.npz")

    total = len(cazuri)
    facute = 0
    lock = Lock()

    def _unul(caz: Caz):
        nonlocal facute
        try:
            rez = pipe.raspunde(caz.intrebare)
            eticheta, detaliu = _clasifica(caz, rez)
        except Exception as exc:  # nu ascundem esecurile de infrastructura
            eticheta, detaliu, rez = "EROARE", f"{type(exc).__name__}: {exc}"[:120], None
        # Progres incremental: o rulare de zeci de minute fara semne de viata
        # nu se poate distinge de una blocata.
        with lock:
            facute += 1
            print(f"[{facute:3d}/{total}] {eticheta:<9} {caz.id:<12} {caz.intrebare[:52]}",
                  flush=True)
        return caz, eticheta, detaliu, rez

    with ThreadPoolExecutor(max_workers=CONCURENTA) as pool:
        rezultate = list(pool.map(_unul, cazuri))

    return {"rezultate": rezultate, "total": len(cazuri)}


def main() -> int:
    limita = int(sys.argv[1]) if len(sys.argv) > 1 else None
    date = ruleaza(limita)
    rez = date["rezultate"]

    pe_tip: dict[str, Counter] = {}
    for caz, eticheta, _, _ in rez:
        pe_tip.setdefault(caz.tip, Counter())[eticheta] += 1

    coloane = ["CORECT", "FALS", "REFUZ_BUN", "REFUZ_RAU", "EROARE"]
    print("=== EVALUARE CAP LA CAP ===\n")
    print(f"{'tip':<10}{'n':>5}  " + "".join(f"{c:>11}" for c in coloane))
    for tip, c in sorted(pe_tip.items()):
        n = sum(c.values())
        print(f"{tip:<10}{n:>5}  " + "".join(f"{c.get(k,0):>11}" for k in coloane))

    total = Counter()
    for c in pe_tip.values():
        total.update(c)
    n = date["total"]
    print(f"{'TOTAL':<10}{n:>5}  " + "".join(f"{total.get(k,0):>11}" for k in coloane))

    fals = total.get("FALS", 0)
    print(f"\nRATA DE RASPUNS FALS: {fals}/{n} = {fals/n*100:.1f}%")
    print(f"Acuratete pe cazuri cu raspuns asteptat: "
          f"{total.get('CORECT',0)}/{n - sum(1 for c,_,_,_ in rez if c.tip=='refuz')}")

    probleme = [(c.id, c.intrebare, e, d) for c, e, d, _ in rez
                if e in ("FALS", "REFUZ_RAU", "EROARE")]
    if probleme:
        print(f"\n=== {len(probleme)} cazuri problematice ===")
        for cid, intrebare, eticheta, detaliu in probleme[:15]:
            print(f"  [{eticheta:<9}] {cid} {intrebare[:52]}")
            print(f"              {detaliu[:95]}")

    out = ROOT / "data" / "eval-rezultate.json"
    out.write_text(json.dumps(
        [{"id": c.id, "tip": c.tip, "intrebare": c.intrebare, "rezultat": e, "detaliu": d}
         for c, e, d, _ in rez], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrezultate scrise in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
