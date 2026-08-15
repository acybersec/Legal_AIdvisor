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

### API

| | |
|---|---|
| `POST /intreaba` | Întrebare, răspuns verificat. Sincron, ~17 secunde. |
| `POST /analizeaza` | Încarcă un document. Răspunde **imediat**, 202, cu un identificator. |
| `GET /analiza/{id}` | Starea analizei plus clauzele terminate **până acum**. |
| `GET /sanatate` | Starea corpusului. |

Analiza de document e asincronă dintr-un motiv practic: fiecare clauză costă un apel de
generare plus două de verificare, aproximativ 17 secunde, iar implicit se analizează 6 clauze.
O cerere sincronă ar ține conexiunea deschisă două minute, iar proxy-urile taie conexiunile
inactive între 30 și 60 de secunde. Măsurat, încărcarea răspunde acum în **5 milisecunde**.

Interogarea întoarce rezultatele **parțiale** deliberat. Clientul vede prima clauză după
~17 secunde în loc de două minute, iar o analiză lentă devine vizibil diferită de una blocată.

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
| Reordonare | `qwen3:14b` | e judecată, nu fluență: „răspunde la întrebare" vs „vorbește despre subiect" |
| Verificare, ancorare | `qwen3:14b` | aici se decide dacă un răspuns pleacă la client |
| Verificare, adversarial | `llama3.1:8b` | caută erori; un model suspicios e acceptabil în rolul ăsta |

Cele trei modele stau simultan în VRAM, 15,6 GB din 32. `qwen3:30b` nu e o opțiune: are 18,6 GB
pe disc dar cere 33 GB încărcat, fiindcă arhitectura MoE se extinde la încărcare.

**Cei doi verificatori rulează pe modele diferite, deliberat.** Două instanțe ale aceluiași
model greșesc corelat, ceea ce face din „doi verificatori independenți" o formalitate.

Repartizarea vine dintr-o măsurătoare, nu dintr-o preferință: refuzurile false observate veneau
aproape toate de la verificatorul de ancorare, care cerea potrivire literală și respingea
reformulări corecte, cu motive de tipul *„nu se regăsește în extras, dar este o traducere a
acesteia"*. Acolo a mers modelul puternic.

Costul întregului lanț, măsurat prin API pe întrebări reale: **aproximativ 17 secunde** de la
întrebare la răspuns verificat de două ori. Regăsire, generare și două verificări.

Inferența rulează pe GPU. Verificat în timpul unei generări active, `size_vram` egal cu
dimensiunea totală a modelului:

```bash
curl -s http://<gazda>:11434/api/ps | python3 -m json.tool | grep size_vram
```

Debit măsurat: `llama3.1:8b` la 229 tokeni/s, `qwen3:14b` la 153. Comanda de mai sus e primul
lucru de rulat când ceva pare inexplicabil de lent — o perioadă întreagă din dezvoltarea acestui
proiect a fost petrecută pe inferență CPU fără ca nimic să dea eroare.

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
MODEL_RERANK=qwen3:14b             # reordonare; gol = strat oprit
MODEL_VERIFICATOR=qwen3:14b        # ancorare
MODEL_VERIFICATOR_2=llama3.1:8b    # adversarial
```

Documentele utilizatorului **nu părăsesc mașina**.

## Cum se evită răspunsurile false

Opt straturi, în ordinea în care intervin:

1. **Fragmentare pe structura legii.** Un articol complet per unitate, cu lanțul de părinți
   atașat. Nu ferestre de dimensiune fixă.
2. **Căutare hibridă.** BM25 lexical plus vectorial, fuzionate cu Reciprocal Rank Fusion.
3. **Potrivire pe prefix** pentru flexiunea românească. FTS5 nu stemuiește, iar fără asta
   „preavizul" nu găsea „preaviz".
4. **Context ierarhic în embedding.** Articolul se indexează împreună cu Titlul și Capitolul
   din care face parte.
5. **Rută deterministă** pentru trimiteri explicite. „articolul 145 din Codul muncii" se
   rezolvă direct din bază, nu semantic.
6. **Reordonare cu model.** Primii opt candidați se dau unui model care îi pune în ordinea în
   care *răspund* la întrebare, nu în care seamănă cu ea. Vezi mai jos de ce contează.
7. **Citări atașate programatic.** Modelul scrie `[S1]`, sistemul substituie citarea stocată.
   O trimitere juridică scrisă de model și absentă din surse oprește răspunsul.
8. **Doi verificatori independenți** plus refuz explicit.

### De ce stratul 6 există

Întrebarea *„Ce se consideră timp de muncă?"* scotea art. 111 pe **locul opt din opt**.
Art. 111 spune, textual, *„Timpul de muncă reprezintă orice perioadă în care salariatul
prestează munca"*. Deasupra lui stăteau art. 113, despre repartizarea timpului de muncă, și
art. 130, despre norma de muncă.

Cauza nu e un bug, e felul în care funcționează BM25: scorul crește cu frecvența termenului.
Un articol care **folosește** repetat „timpul de muncă" bate articolul care îl **definește** o
singură dată. Pentru un corpus juridic asta e sistematic — articolele de definiții sunt scurte
și enunță termenul o dată.

Nici regăsirea vectorială nu repară asta singură: art. 111 și 113 sunt semantic apropiate,
vorbesc despre același subiect. Diferența dintre *„vorbește despre X"* și *„răspunde la
întrebarea despre X"* cere citirea ambelor texte în raport cu întrebarea.

**Contractul stratului: nu poate face rezultatul mai prost.** Orice ieșire pe care sistemul nu
o înțelege — JSON invalid, indici inventați, model căzut, timeout — înseamnă păstrarea ordinii
primite, iar un candidat pe care modelul nu îl menționează se adaugă la coadă, nu se pierde.
Șase teste acoperă exact aceste căi. Se oprește complet cu `MODEL_RERANK=`.

## Cifre măsurate

Toate de mai jos vin din rulări reale, încheiate. Ce nu s-a măsurat e spus ca atare.

### Cap la cap — suita completă, 105 cazuri, zero erori

| Tip de caz | Cazuri | Corect | **Răspuns fals** | Refuz corect | Refuz greșit |
|---|---:|---:|---:|---:|---:|
| Conținut, întrebare naturală | 20 | 18 | **2** | — | 0 |
| Lookup, trimitere la articol | 75 | 75 | **0** | — | 0 |
| Trebuia refuzat | 10 | — | **0** | 10 | — |
| **Total** | **105** | **93** | **2 = 1,9%** | **10/10** | **0** |

Cifra care contează pentru un produs juridic este a treia coloană. Un refuz costă un client
nemulțumit; un răspuns fals costă un client care ia o decizie greșită și dă vina pe tine.

**Zero refuzuri false și 10 din 10 refuzuri corecte** — sistemul tace exact când trebuie și
numai când trebuie.

Ce sunt cele două răspunsuri false, examinate individual:

- *„Pot să renunț la concediul de odihnă în schimbul unor bani?"* — a citat art. 146, care
  prevede că **compensarea în bani e permisă doar la încetarea contractului**. Răspunsul e
  corect juridic; setul aștepta art. 144, care spune același lucru mai direct. Etichetă strictă,
  nu defect.
- *„Ce se consideră timp de muncă?"* — a redat **definiția din art. 111 dar a atribuit-o
  art. 113**. Ăsta e un defect real de regăsire, exact slăbiciunea documentată mai jos la
  *Fără reranker*: articolul care **definește** un termen nu e lexical distinctiv față de cele
  care îl folosesc.

### Regăsire — suita completă, 95 de cazuri, rulare încheiată

| Tip de caz | Cazuri | recall@1 | recall@3 | recall@5 |
|---|---:|---:|---:|---:|
| Conținut, întrebare în limbaj natural | 20 | **85,0%** | 90,0% | 95,0% |
| Lookup, trimitere explicită la articol | 75 | **100%** | 100% | 100% |
| Total | 95 | 96,8% | 97,9% | 98,9% |

Progresia care a produs cifra de conținut, fiecare pas după un diagnostic:

| Etapă | recall@1 | recall@5 |
|---|---:|---:|
| RRF cu ponderi egale | 65,0% | 85,0% |
| RRF ponderat | 75,0% | 90,0% |
| Plus potrivire pe prefix pentru flexiune | **85,0%** | **95,0%** |

### Cum se reproduce

Setul de evaluare are 105 cazuri, fiecare cu articol-sursă verificat că există exact o dată în
bază. Rulează cu:

```bash
MODEL_VERIFICATOR=qwen3:14b MODEL_VERIFICATOR_2=llama3.1:8b \
  .venv/bin/python -m app.eval.end_to_end             # tot setul
.venv/bin/python -m app.eval.end_to_end --doar-model  # doar cele 30 care depind de model
```

### O capcană de măsurare, dacă schimbi modelele

O rulare anterioară raporta **7 refuzuri false**. Cinci dintre ele erau răspunsuri corecte pe
care detectorul de citări le respingea **fiindcă citau corect**: compara șiruri, iar textul unui
articol nu-și repetă niciodată propriul număr — acela stă în etichetă. Deci „Conform articolului
44" era declarat invenție chiar când sursa ERA articolul 44.

Merită reținut ca metodă: când o gardă de siguranță respinge mult, verifică întâi garda, nu
modelul.

## Ce nu face încă

Lista e completă și onestă. Citește-o înainte de a promite ceva unui client.

- **Acoperă trei coduri.** Codul civil, dreptul comercial, jurisprudența și normele metodologice
  nu sunt indexate. O întrebare din afara corpusului primește refuz, corect, dar clientul poate
  aștepta altceva.
- **Fără autentificare, fără multi-tenant, fără audit trail.** MVP local, conform brief.
- **Fără OCR.** PDF-urile scanate sunt respinse explicit, nu procesate gol.
- **Fără urmărirea modificărilor legislative.** Corpusul e o fotografie la data ingestiei.
  Reingestia e manuală.
- **Un apel de inferență blocat ocupă lucrătorul până la repornire.** Analiza rulează pe un
  singur fir, iar Python nu poate întrerupe un fir blocat. Jobul e declarat eșuat la termen și
  clientul află, dar lucrătorul rămâne ocupat. Repararea completă cere izolare în alt proces.
- **Randarea pe ecran îngust nu a fost verificată vizual.** Punctul de rupere la 560px
  există și e prezent în CSS-ul servit, verificat prin `document.styleSheets` în browser, dar
  nimeni nu a văzut pagina la lățime de telefon: compozitorul Wayland de pe mașina de
  dezvoltare maximizează orice fereastră Chrome, deci un viewport de 430px nu s-a putut obține.
  Interfața pe desktop, în schimb, e verificată în Chrome real, inclusiv fluxul complet de
  întrebare, răspuns, cele două verdicte și afișarea temeiului legal.
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

## Depanare

Două defecte care arată identic cu un cod stricat, ambele întâlnite pe acest proiect.

**Serverul de inferență răspunde la `/api/tags` dar nu generează.** Controlul răspunde în
milisecunde, modelele apar încărcate în VRAM, iar `/api/generate` întoarce 500 sau atârnă.
Nu e o problemă de cod. Verifică întâi:

```bash
curl -s -m 5 http://<gazda>:11434/api/tags | head -c 80        # controlul
curl -s -m 30 http://<gazda>:11434/api/generate \
  -d '{"model":"llama3.1:8b","prompt":"ok","stream":false}'    # inferenta
```

Dacă primul răspunde și al doilea nu, repornește Ollama pe gazdă. Nimic din acest repo nu
repară asta.

**Un timeout de citire nu este un termen limită.** `httpx` aplică `timeout` per *operație* de
citire. Dacă serverul din amonte ține conexiunea deschisă fără să răspundă, cronometrul se
resetează și apelul nu expiră niciodată. Măsurat aici: conexiune `ESTABLISHED` către Ollama,
zero octeți în coadă, deschisă **peste opt minute**, cu timeout de 180 de secunde.

Diagnostic:

```bash
ss -tnp | grep 11434    # conexiuni deschise catre serverul de inferenta
```

De aceea analiza de document are un termen limită propriu, la nivel de job, independent de
timeout-ul HTTP. Se reglează cu `TERMEN_ANALIZA_S`.
