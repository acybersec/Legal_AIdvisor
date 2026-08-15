"use client";

import { useEffect, useState } from "react";

const API = process.env.API_URL ?? "http://127.0.0.1:8000";

type Sursa = { citare: string; cale: string; extras: string };
type Verificator = { nume: string; trecut: boolean; motiv: string };
type Raspuns = {
  intrebare: string;
  a_refuzat: boolean;
  raspuns: string;
  motiv_refuz: string;
  surse: Sursa[];
  verificatori: Verificator[];
};

const EXEMPLE = [
  "Cate zile de concediu de odihna am minim pe an?",
  "Care este cota impozitului pe profit?",
  "Cat poate dura o delegare fara acordul salariatului?",
  "Cat este salariul minim brut in 2026, in lei?",
];

type ClauzaAnalizata = {
  index: number;
  fragment: string;
  acoperita: boolean;
  observatie: string;
  surse: { citare: string; cale: string }[];
};

type Raport = {
  id: string;
  stare: "in_lucru" | "gata" | "esuat";
  eroare: string;
  nume_fisier: string;
  clauze_total: number;
  clauze_gata: number;
  clauze_acoperite: number;
  avertisment: string;
  rezultate: ClauzaAnalizata[];
};

// Cat de des intrebam serverul de stare.
//
// O clauza costa aproximativ 17 secunde, deci mai des de doua secunde ar fi
// doar trafic. Mai rar ar face progresul sacadat si ar strica exact lucrul
// pentru care exista interogarea: sa se vada ca sistemul lucreaza.
const PAS_INTEROGARE_MS = 2000;

// Dupa atat renuntam si spunem asta. Fara plafon, o analiza care nu se mai
// termina ar lasa interfata sa se invarta la nesfarsit.
const RABDARE_MS = 15 * 60 * 1000;

export default function Pagina() {
  const [intrebare, setIntrebare] = useState("");
  const [rezultat, setRezultat] = useState<Raspuns | null>(null);
  const [lucreaza, setLucreaza] = useState(false);
  const [eroare, setEroare] = useState("");
  const [stare, setStare] = useState<{ articole: number } | null>(null);
  const [raport, setRaport] = useState<Raport | null>(null);
  const [analizeaza, setAnalizeaza] = useState(false);

  // Analiza e asincrona: incarcarea intoarce un identificator, iar rezultatele
  // se cer periodic. Clauzele apar pe rand, nu toate la sfarsit, ca sa se vada
  // ca sistemul lucreaza. Vezi backend/app/documente/joburi.py.
  async function incarcaDocument(fisier: File) {
    setAnalizeaza(true);
    setEroare("");
    setRaport(null);
    setRezultat(null);
    const date = new FormData();
    date.append("fisier", fisier);
    try {
      const r = await fetch(`${API}/analizeaza`, { method: "POST", body: date });
      const pornit = await r.json();
      if (!r.ok) throw new Error(pornit.detail ?? `Serverul a raspuns ${r.status}`);

      const pana = Date.now() + RABDARE_MS;
      for (;;) {
        const s = await fetch(`${API}/analiza/${pornit.id}`);
        if (!s.ok) throw new Error("Analiza nu mai poate fi gasita pe server.");
        const d: Raport = await s.json();
        setRaport(d);
        if (d.stare === "esuat") throw new Error(d.eroare || "Analiza a esuat.");
        if (d.stare === "gata") break;
        if (Date.now() > pana) {
          throw new Error("Analiza dureaza neobisnuit de mult. Verifica serverul de inferenta.");
        }
        await new Promise((r) => setTimeout(r, PAS_INTEROGARE_MS));
      }
    } catch (e) {
      setEroare(e instanceof Error ? e.message : "Eroare la analiza documentului");
    } finally {
      setAnalizeaza(false);
    }
  }

  useEffect(() => {
    fetch(`${API}/sanatate`)
      .then((r) => r.json())
      .then(setStare)
      .catch(() => setStare(null));
  }, []);

  async function intreaba(text: string) {
    if (!text.trim() || lucreaza) return;
    setLucreaza(true);
    setEroare("");
    setRezultat(null);
    try {
      const r = await fetch(`${API}/intreaba`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error(`Serverul a raspuns ${r.status}`);
      setRezultat(await r.json());
    } catch (e) {
      setEroare(e instanceof Error ? e.message : "Eroare necunoscuta");
    } finally {
      setLucreaza(false);
    }
  }

  return (
    <main className="wrap">
      <header>
        <h1>Legal_AIdvisor</h1>
        <p className="sub">
          Raspunsuri ancorate in legislatia romaneasca. Fiecare afirmatie citeaza
          articolul din care provine, iar citarea vine din baza de date, nu de la model.
        </p>
        {stare && (
          <p className="stat">
            {stare.articole} articole indexate · Codul muncii · Codul fiscal ·
            Cod procedura fiscala
          </p>
        )}
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          intreaba(intrebare);
        }}
      >
        <input
          type="text"
          value={intrebare}
          onChange={(e) => setIntrebare(e.target.value)}
          placeholder="Pune o intrebare despre dreptul muncii sau fiscal..."
          disabled={lucreaza}
        />
        <button type="submit" disabled={lucreaza || !intrebare.trim()}>
          {lucreaza ? "Verific..." : "Intreaba"}
        </button>
      </form>

      <div className="exemple">
        {EXEMPLE.map((e) => (
          <button
            key={e}
            type="button"
            onClick={() => {
              setIntrebare(e);
              intreaba(e);
            }}
            disabled={lucreaza}
          >
            {e}
          </button>
        ))}
      </div>

      <div className="incarcare">
        <label>
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md"
            disabled={analizeaza || lucreaza}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) incarcaDocument(f);
              e.target.value = "";
            }}
          />
          <span>
            {analizeaza
              ? raport
                ? `Analizez clauza ${raport.clauze_gata + 1} din ${raport.clauze_total}...`
                : "Pregatesc documentul..."
              : "sau incarca un contract: PDF, DOCX, TXT"}
          </span>
        </label>
      </div>

      {eroare && (
        <div className="card refuz">
          <div className="eticheta">Eroare</div>
          <p className="raspuns">{eroare}</p>
        </div>
      )}

      {raport && (
        <>
          <div className="card">
            <div className="eticheta">Analiza document</div>
            <p className="raspuns">
              {raport.nume_fisier} — {raport.clauze_gata} din {raport.clauze_total}{" "}
              clauze analizate, {raport.clauze_acoperite} cu temei legal identificat.
            </p>
            <p className="motiv">{raport.avertisment}</p>
          </div>
          {raport.rezultate.map((c) => (
            <div className={`card${c.acoperita ? "" : " refuz"}`} key={c.index}>
              <div className="eticheta">
                Clauza {c.index} · {c.acoperita ? "temei identificat" : "neacoperita"}
              </div>
              <div className="extras" style={{ marginBottom: 12 }}>
                {c.fragment}
              </div>
              <p className="raspuns" style={{ fontSize: 16 }}>
                {c.observatie}
              </p>
              {c.surse.map((s) => (
                <div className="sursa" key={s.citare}>
                  <div className="citare">{s.citare}</div>
                  <div className="cale">{s.cale}</div>
                </div>
              ))}
            </div>
          ))}
        </>
      )}

      {rezultat && (
        <>
          <div className={`card${rezultat.a_refuzat ? " refuz" : ""}`}>
            <div className="eticheta">
              {rezultat.a_refuzat ? "Raspuns refuzat" : "Raspuns verificat"}
            </div>
            <p className="raspuns">{rezultat.raspuns}</p>
            {rezultat.a_refuzat && rezultat.motiv_refuz && (
              <p className="motiv">motiv: {rezultat.motiv_refuz}</p>
            )}
            {rezultat.verificatori.length > 0 && (
              <div className="verif">
                {rezultat.verificatori.map((v) => (
                  <span
                    key={v.nume}
                    className={`pill ${v.trecut ? "ok" : "nu"}`}
                    title={v.motiv}
                  >
                    {v.nume}: {v.trecut ? "trecut" : "cazut"}
                  </span>
                ))}
              </div>
            )}
          </div>

          {rezultat.surse.length > 0 && (
            <div className="card">
              <div className="eticheta">Temei legal</div>
              {rezultat.surse.map((s) => (
                <div className="sursa" key={s.citare}>
                  <div className="citare">{s.citare}</div>
                  <div className="cale">{s.cale}</div>
                  <div className="extras">{s.extras}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <footer>
        Informare juridica, nu consultanta. Nu inlocuieste un avocat. Verifica
        intotdeauna forma in vigoare in Monitorul Oficial.
      </footer>
    </main>
  );
}
