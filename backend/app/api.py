"""
API-ul Legal_AIdvisor. FastAPI, fara autentificare in MVP-ul local.

Contractul cu frontend-ul: raspunsul spune INTOTDEAUNA daca a fost livrat sau
refuzat, si de ce. Un client care plateste abonament trebuie sa vada cand
sistemul nu stie, nu sa primeasca tacere sau o inventie.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .answer.pipeline import Pipeline
from .documente.analiza import analizeaza_clauza
from .documente.extrage import DocumentIlizibil, extrage_text, imparte_in_clauze
from .documente.joburi import GATA, IN_LUCRU, Registru

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "legal.db"
INDEX = ROOT / "data" / "vectors.npz"

app = FastAPI(
    title="Legal_AIdvisor",
    description="Asistent juridic ancorat in legislatia romaneasca, cu citari verificate.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_pipeline: Pipeline | None = None
registru = Registru()


def pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        if not DB.exists() or not INDEX.exists():
            raise HTTPException(
                503,
                "Corpusul nu este pregatit. Ruleaza ingestia si constructia indexului.",
            )
        _pipeline = Pipeline(DB, INDEX)
    return _pipeline


class Intrebare(BaseModel):
    text: str = Field(min_length=3, max_length=1000)


class Sursa(BaseModel):
    citare: str
    cale: str
    extras: str


class RaspunsAPI(BaseModel):
    intrebare: str
    a_refuzat: bool
    raspuns: str
    motiv_refuz: str = ""
    surse: list[Sursa] = []
    verificatori: list[dict] = []


@app.get("/sanatate")
def sanatate() -> dict:
    import sqlite3

    if not DB.exists():
        return {"stare": "corpus lipsa", "articole": 0}
    conn = sqlite3.connect(DB)
    n = conn.execute("SELECT COUNT(*) FROM articole").fetchone()[0]
    acte = conn.execute(
        "SELECT slug, titlu, data_vigoare FROM acte ORDER BY slug"
    ).fetchall()
    conn.close()
    return {
        "stare": "gata" if n else "corpus gol",
        "articole": n,
        "acte": [{"slug": a[0], "titlu": a[1][:80], "vigoare": a[2][:10]} for a in acte],
        "index_vectorial": INDEX.exists(),
    }


@app.post("/intreaba", response_model=RaspunsAPI)
def intreaba(q: Intrebare) -> RaspunsAPI:
    rez = pipeline().raspunde(q.text)
    return RaspunsAPI(
        intrebare=rez.intrebare,
        a_refuzat=rez.a_refuzat,
        raspuns=rez.raspuns,
        motiv_refuz=rez.motiv_refuz,
        surse=[
            Sursa(citare=s.citare, cale=s.cale, extras=s.text[:1200]) for s in rez.surse
        ],
        verificatori=[
            {"nume": v.nume, "trecut": v.trecut, "motiv": v.motiv} for v in rez.verdicte
        ],
    )


MAX_OCTETI = 8 * 1024 * 1024


def _serializeaza(r) -> dict:
    return {
        "index": r.index,
        "fragment": r.fragment,
        "acoperita": r.acoperita,
        "observatie": r.observatie,
        "surse": [{"citare": s.citare, "cale": s.cale} for s in r.surse],
    }


@app.post("/analizeaza", status_code=202)
async def analizeaza_document(fisier: UploadFile = File(...)) -> dict:
    """Preia documentul si intoarce IMEDIAT un identificator de analiza.

    Ce se face aici, sincron, pentru ca e ieftin si pentru ca esecurile trebuie
    sa ajunga la client ca erori HTTP, nu ascunse intr-un job: citirea
    fisierului, verificarea dimensiunii, extragerea textului si impartirea in
    clauze. Toate sunt locale si dureaza milisecunde.

    Ce pleaca in fundal: analiza propriu-zisa, singura parte care cheama modelul
    si singura care dureaza minute. Vezi documente/joburi.py pentru de ce.

    Documentul nu paraseste masina: extragerea e locala, iar catre model pleaca
    doar fragmentul de clauza necesar analizei.
    """
    continut = await fisier.read()
    if len(continut) > MAX_OCTETI:
        raise HTTPException(413, f"Fisier prea mare. Limita este {MAX_OCTETI // 1024 // 1024} MB.")

    nume = fisier.filename or "document"
    try:
        text = extrage_text(nume, continut)
    except DocumentIlizibil as exc:
        raise HTTPException(422, str(exc)) from exc

    clauze = imparte_in_clauze(text)
    if not clauze:
        raise HTTPException(422, "Nu am gasit clauze suficient de lungi pentru analiza.")

    pipe = pipeline()  # ridicat aici: un corpus lipsa e 503 imediat, nu job esuat
    job = registru.creeaza(nume, len(text), len(clauze))

    def _lucru() -> None:
        for clauza in clauze:
            registru.adauga_rezultat(job.id, analizeaza_clauza(pipe, clauza))

    registru.porneste(job.id, _lucru)
    return {
        "id": job.id,
        "stare": job.stare,
        "nume_fisier": job.nume_fisier,
        "caractere": job.caractere,
        "clauze_total": job.clauze_total,
    }


@app.get("/analiza/{job_id}")
def stare_analiza(job_id: str) -> dict:
    """Starea analizei, cu clauzele terminate pana acum.

    Intoarce rezultatele PARTIALE deliberat: clientul vede prima clauza dupa
    aproximativ 17 secunde in loc de doua minute, iar o analiza lenta devine
    vizibil diferita de una blocata.
    """
    job = registru.ia(job_id)
    if job is None:
        raise HTTPException(404, "Analiza nu exista sau a fost sters raportul ei.")

    acoperite = sum(1 for r in job.rezultate if r.acoperita)
    return {
        "id": job.id,
        "stare": job.stare,
        "eroare": job.eroare,
        "nume_fisier": job.nume_fisier,
        "caractere": job.caractere,
        "clauze_total": job.clauze_total,
        "clauze_gata": job.clauze_gata,
        "clauze_acoperite": acoperite,
        "avertisment": (
            "Analiza acopera doar clauzele afisate si doar codurile indexate. "
            "Nu inlocuieste citirea contractului de catre un avocat."
            if job.stare == GATA else ""
        ),
        "rezultate": [_serializeaza(r) for r in job.rezultate],
    }
