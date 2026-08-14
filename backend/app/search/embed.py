"""
Index vectorial peste corpus, cu embeddings de la Ollama.

Doua decizii:

1. Modelul e bge-m3, multilingv. Modelele de embedding antrenate pe engleza dau
   rezultate slabe pe romana juridica. Vezi TRNPRE-67.

2. Textul trimis la embedding include CALEA ierarhica, nu doar corpul
   articolului. Un articol rupt de Titlul si Capitolul din care face parte isi
   pierde contextul: "art. 44" nu spune nimic, "Titlul II Contractul individual
   de munca > Capitolul III Modificarea contractului > Articolul 44" spune tot.
   Este varianta deterministă si gratuita a Contextual Retrieval, care in
   masuratorile Anthropic scade rata de esec la regasire cu 35%. Vezi
   CERCETARE.md sectiunea 5, stratul 3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import numpy as np

import os

OLLAMA = os.environ.get("OLLAMA_URL", "http://10.0.1.123:11434")
MODEL = "bge-m3"
DIM = 1024
# Cat text de articol intra in embedding. Articolele lungi din Codul fiscal
# depasesc fereastra utila; primele 2000 de caractere contin norma, restul e
# de regula enumerare de exceptii.
MAX_CHARS = 2000


def embed(texts: list[str], *, batch: int = 32, timeout: float = 180.0) -> np.ndarray:
    """Vectori normalizati L2, ca produsul scalar sa fie direct cosinus."""
    out: list[list[float]] = []
    with httpx.Client(timeout=timeout) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            r = client.post(f"{OLLAMA}/api/embed", json={"model": MODEL, "input": chunk})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    arr = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def text_pentru_embedding(row: sqlite3.Row) -> str:
    """Calea ierarhica plus textul. Vezi nota 2 din antetul modulului."""
    parts = [row["cale"]]
    if row["denumire"]:
        parts.append(row["denumire"])
    parts.append(row["text"][:MAX_CHARS])
    return "\n".join(p for p in parts if p)


def construieste_index(db_path: str | Path, out_path: str | Path) -> tuple[int, int]:
    """Embed pentru toate articolele. Intoarce (nr_articole, dimensiune)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, cale, denumire, text FROM articole ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        raise RuntimeError("Baza nu contine articole. Ruleaza intai ingestia.")

    vectors = embed([text_pentru_embedding(r) for r in rows])
    ids = np.asarray([r["id"] for r in rows], dtype=np.int64)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, ids=ids, vectors=vectors, model=np.array([MODEL]))
    return len(rows), vectors.shape[1]


def incarca_index(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    data = np.load(path, allow_pickle=False)
    return data["ids"], data["vectors"]


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    n, dim = construieste_index(root / "data" / "legal.db", root / "data" / "vectors.npz")
    print(f"index vectorial: {n} articole, {dim} dimensiuni, model {MODEL}")
