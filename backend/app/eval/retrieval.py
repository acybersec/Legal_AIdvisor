"""
Masoara regasirea pe setul de evaluare. ISC-6, partea de retrieval.

Metricile se raporteaza SEPARAT pe tip de caz. Un numar agregat ar ascunde
exact ce trebuie vazut: cazurile de lookup au ruta deterministă si vor fi
aproape perfecte, iar amestecul lor in medie ar masca performanta reala pe
intrebarile de continut, care sunt cele grele.

Cazurile de refuz nu se masoara aici. Ele testeaza comportamentul lantului de
raspuns, nu al regasirii, si se evalueaza dupa ce exista verificatorii.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.eval.cases import toate
from app.search.retrieve import Retriever

ROOT = Path(__file__).resolve().parents[3]


def ruleaza(k_values: tuple[int, ...] = (1, 3, 5)) -> dict:
    retr = Retriever(ROOT / "data" / "legal.db", ROOT / "data" / "vectors.npz")
    cazuri = [c for c in toate(str(ROOT / "data" / "legal.db")) if c.tip != "refuz"]

    max_k = max(k_values)
    rezultate: dict[str, dict[int, int]] = {}
    esecuri: list[tuple[str, str, str, list[str]]] = []

    for caz in cazuri:
        hits = retr.cauta(caz.intrebare, k=max_k)
        gasite = [(h.act_slug, h.numar) for h in hits]
        tinta = (caz.act, caz.articol)
        bucket = rezultate.setdefault(caz.tip, {kk: 0 for kk in k_values})
        for kk in k_values:
            if tinta in gasite[:kk]:
                bucket[kk] += 1
        if tinta not in gasite[: max(k_values)]:
            esecuri.append((caz.id, caz.intrebare, f"{caz.act}#{caz.articol}",
                            [f"{a}#{n}" for a, n in gasite[:3]]))

    total_pe_tip = {}
    for caz in cazuri:
        total_pe_tip[caz.tip] = total_pe_tip.get(caz.tip, 0) + 1

    return {"rezultate": rezultate, "total": total_pe_tip, "esecuri": esecuri,
            "k_values": k_values}


def main() -> int:
    r = ruleaza()
    print("=== REGASIRE, recall@k pe tip de caz ===\n")
    print(f"{'tip':<10} {'cazuri':>7}  " + "  ".join(f"@{k:<5}" for k in r["k_values"]))
    for tip, buckets in sorted(r["rezultate"].items()):
        n = r["total"][tip]
        cifre = "  ".join(f"{buckets[k]/n*100:5.1f}%" for k in r["k_values"])
        print(f"{tip:<10} {n:>7}  {cifre}")

    total = sum(r["total"].values())
    agg = {k: sum(b[k] for b in r["rezultate"].values()) for k in r["k_values"]}
    cifre = "  ".join(f"{agg[k]/total*100:5.1f}%" for k in r["k_values"])
    print(f"{'TOTAL':<10} {total:>7}  {cifre}")

    if r["esecuri"]:
        print(f"\n=== {len(r['esecuri'])} esecuri, primele 10 ===")
        for cid, intrebare, tinta, gasite in r["esecuri"][:10]:
            print(f"  [{cid}] {intrebare[:58]}")
            print(f"        asteptat {tinta} | primele: {', '.join(gasite)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
