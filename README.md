# Legal_AIdvisor

Asistent juridic ancorat în legislația românească. Răspunde la întrebări despre **dreptul
muncii** și **fiscal-contabil**, citând articolul din care provine fiecare afirmație.

Diferența față de un chatbot peste PDF-uri: **citarea nu vine de la model**. Este compusă la
ingestie și stocată în baza de date, iar modelul poate scrie doar un marcaj de sursă. Dacă
scrie totuși o trimitere juridică proprie, răspunsul este oprit.

## Stare: în lucru

MVP local, fără autentificare. Vezi *Ce nu face încă* mai jos, înainte de a-l arăta unui client.

## Ce face

- **Întrebare cu răspuns citat.** Întrebi în română, primești răspuns cu articolul exact.
- **Verificare dublă.** Fiecare răspuns trece prin doi verificatori independenți. Dacă oricare
  cade, răspunsul **nu se trimite** și sistemul refuză explicit, cu motiv.
- **Analiză de document.** Încarci un contract, fiecare clauză trece prin același lanț.
- **Refuz calibrat.** Când corpusul nu conține răspunsul, sistemul spune asta. Nu inventează.

## Corpusul

| Act | Articole | În vigoare din |
|---|---:|---|
| Codul muncii, Legea 53/2003 republicată | 297 | 2011-05-18 |
| Codul fiscal, Legea 227/2015 | 645 | 2016-01-01 |
| Codul de procedură fiscală, Legea 207/2015 | 430 | 2016-01-01 |
| **Total** | **1372** | |

Sursa este `legislatie.just.ro`. Textul se ia din paginile HTML publice, care conțin **forma
consolidată la zi**, nu din API-ul SOAP, care întoarce forma de la data republicării. API-ul se
folosește doar pentru descoperirea actelor și metadate. Detalii și capcane:
`API-LEGISLATIE.md`.

Reutilizarea textelor oficiale este liberă conform Legii 8/1996 art. 9.

## Cum se rulează

### Cu Docker

```bash
docker compose up --build
```

Frontend pe `http://localhost:3000`, API pe `http://localhost:8000`.

Corpusul se montează din `./data`. Dacă lipsește, generează-l întâi cu pașii de mai jos.

### Local, fără Docker

```bash
# 1. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Ingestia corpusului, aproximativ 2 minute
.venv/bin/python -m app.ingest.run

# 3. Indexul vectorial, aproximativ 25 de secunde pe GPU
.venv/bin/python -m app.search.embed

# 4. API
.venv/bin/uvicorn app.api:app --port 8000

# 5. Frontend, in alt terminal
cd frontend && npm install && npm run build && npx next start
```

### Modele

| Rol | Model | Unde |
|---|---|---|
| Embeddings | `bge-m3`, multilingv, 1024 dimensiuni | Ollama, local |
| Generare | `llama3.1:8b` | Ollama, local |
| Verificare | `llama3.1:8b` | Ollama, local |

Adresa Ollama se schimbă din `OLLAMA_URL`. Documentele utilizatorului **nu părăsesc mașina**.

## Cum se evită răspunsurile false

Șapte straturi, în ordinea în care intervin:

1. **Fragmentare pe structura legii.** Un articol complet per unitate, cu lanțul de părinți
   atașat. Nu ferestre de dimensiune fixă.
2. **Căutare hibridă.** BM25 lexical plus vectorial, fuzionate cu Reciprocal Rank Fusion.
3. **Potrivire pe prefix** pentru flexiunea românească. FTS5 nu stemuiește, iar fără asta
   „preavizul" nu găsea „preaviz".
4. **Context ierarhic în embedding.** Articolul se indexează împreună cu Titlul și Capitolul
   din care face parte.
5. **Rută deterministă** pentru trimiteri explicite. „articolul 145 din Codul muncii" se
   rezolvă direct din bază, nu semantic.
6. **Citări atașate programatic.** Modelul scrie `[S1]`, sistemul substituie citarea stocată.
   O trimitere juridică scrisă de model și absentă din surse oprește răspunsul.
7. **Doi verificatori independenți** plus refuz explicit.

## Ce nu face încă

Lista e completă și onestă. Citește-o înainte de a promite ceva unui client.

- **Acoperă trei coduri.** Codul civil, dreptul comercial, jurisprudența și normele metodologice
  nu sunt indexate. O întrebare din afara corpusului primește refuz, corect, dar clientul poate
  aștepta altceva.
- **Cei doi verificatori rulează pe același model.** Două instanțe ale aceluiași model pot greși
  identic, deci garanția e mai slabă decât sună „doi verificatori independenți". Interfața
  primește modelul ca parametru, ca al doilea să poată fi mutat pe un model mai puternic.
- **Fără autentificare, fără multi-tenant, fără audit trail.** MVP local, conform brief.
- **Fără OCR.** PDF-urile scanate sunt respinse explicit, nu procesate gol.
- **Fără urmărirea modificărilor legislative.** Corpusul e o fotografie la data ingestiei.
  Reingestia e manuală.
- **Fără reranker.** Cazurile unde mai multe articole vorbesc despre același subiect, iar cel
  care îl *definește* nu e distinctiv lexical, rămân o slăbiciune cunoscută.
- **`llama3.1:8b` e modest pe română juridică.** Răspunsurile sunt corecte dar seci. Un model
  mai puternic ar îmbunătăți vizibil calitatea, fără schimbări de arhitectură.

## Avertisment juridic

Informare juridică, nu consultanță. Nu înlocuiește un avocat. Verifică întotdeauna forma în
vigoare în Monitorul Oficial.

## Licență

[MIT](LICENSE) © 2026 Radu Socoliuc
