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

| Rol | Model | De ce acesta |
|---|---|---|
| Embeddings | `bge-m3`, 1024 dim | multilingv; modelele antrenate pe engleză sunt slabe pe română juridică |
| Generare | `llama3.1:8b` | fluența costă puțin; două propoziții corecte în română nu cer un model mare |
| Verificare, ancorare | `qwen3:30b` | aici se decide dacă un răspuns pleacă la client |
| Verificare, adversarial | `llama3.1:8b` | caută erori; un model suspicios e acceptabil în rolul ăsta |

**Cei doi verificatori rulează pe modele diferite, deliberat.** Două instanțe ale aceluiași
model greșesc corelat, ceea ce face din „doi verificatori independenți" o formalitate.

Repartizarea vine dintr-o măsurătoare, nu dintr-o preferință: refuzurile false observate veneau
aproape toate de la verificatorul de ancorare, care cerea potrivire literală și respingea
reformulări corecte, cu motive de tipul *„nu se regăsește în extras, dar este o traducere a
acesteia"*. Acolo a mers modelul puternic.

Costul: verificarea de ancorare durează aproximativ 34 de secunde, față de câteva secunde pe
modelul mic.

> ### ⚠️ Cifrele de performanță din acest document sunt măsurate pe CPU
>
> Mașina de inferență are o GTX 5090, dar Ollama de pe ea **nu o folosește**. Verificat în
> timpul unei generări active: `size_vram` este `0.0 GB` pentru toate modelele încărcate, deși
> suma cerută, 27,2 GB, încape în cei 32 ai plăcii.
>
> ```bash
> curl -s http://<gazda>:11434/api/ps | python3 -m json.tool | grep size_vram
> ```
>
> Toate duratele de mai jos — 34 de secunde per verificare, minute per document — descriu
> inferență pe procesor. Pe GPU, aceleași modele sunt de ordinul zecilor de ori mai rapide.
>
> **De verificat pe mașina de inferență:** drivere CUDA instalate, Ollama compilat cu suport
> GPU, și dacă rulează într-un container care nu vede placa.
>
> Merită știut și pentru diagnostic: am schimbat modelul de verificare de la 30B la 14B crezând
> că rezolv o depășire de VRAM. Depășirea era de RAM de sistem. Diagnosticul era greșit, chiar
> dacă reparația s-a nimerit utilă — pe CPU un model mai mic chiar e mai rapid.

### Două capcane care arată exact ca defecțiuni

**Modelele cu mod de gândire** — `qwen3` între ele — consumă tot bugetul de tokeni pe raționament
intern și întorc răspuns **gol** dacă nu le dezactivezi explicit. Măsurat: 48 de secunde și zero
caractere. De aceea fiecare cerere de verificare trimite `think: false`.

**Dimensiunea pe disc nu prezice dimensiunea în VRAM.** `qwen3:30b` are 18,6 GB pe disc și cere
**33 GB** încărcat — nu încape pe o placă de 32 GB, nici cu `num_ctx` redus la 4096. Arhitectura
MoE se extinde la încărcare.

Ollama nu refuză cererea: împinge straturi pe CPU și continuă. Rezultatul a fost o evaluare care
a procesat **un singur caz în 18 minute**, fără nicio eroare în log. Arăta identic cu un blocaj
de cod.

Diagnosticul care economisește ore: când o sarcină pe GPU e absurd de lentă **fără să dea
erori**, verifică întâi memoria, nu logica.

```bash
curl -s http://<gazda>:11434/api/ps | python3 -m json.tool   # suma campurilor size
```

Dacă suma depășește VRAM-ul fizic, ai găsit cauza.

Se schimbă din mediu, fără atingerea codului:

```bash
OLLAMA_URL=http://10.0.1.123:11434
MODEL_VERIFICATOR=qwen3:30b        # ancorare
MODEL_VERIFICATOR_2=llama3.1:8b    # adversarial
```

Documentele utilizatorului **nu părăsesc mașina**.

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
- **Analiza de document este sincronă și lentă.** Fiecare clauză costă un apel de generare plus
  două de verificare, aproximativ 20 de secunde pe modelul local. Implicit se analizează 6
  clauze, deci în jur de 2 minute per document. Pentru producție e nevoie de job asincron cu
  identificator și interogare de stare, nu de un număr mai mic de clauze.
- **Interfața nu a fost verificată vizual.** Build-ul trece și serverul întoarce HTTP 200 cu
  conținutul așteptat, dar nimeni nu a văzut pagina randată. Aspectul, tema întunecată și
  comportamentul pe ecran îngust sunt neverificate.
- **`docker compose` nu a fost rulat.** Docker nu era instalat pe mașina de dezvoltare.
  Fișierele sunt scrise, dar un build netestat ascunde de regulă cel puțin o eroare. Rularea
  locală, fără Docker, este calea verificată.
- **`llama3.1:8b` e modest pe română juridică.** Răspunsurile sunt corecte dar seci. Un model
  mai puternic ar îmbunătăți vizibil calitatea, fără schimbări de arhitectură.

## Avertisment juridic

Informare juridică, nu consultanță. Nu înlocuiește un avocat. Verifică întotdeauna forma în
vigoare în Monitorul Oficial.

## Licență

[MIT](LICENSE) © 2026 Radu Socoliuc
