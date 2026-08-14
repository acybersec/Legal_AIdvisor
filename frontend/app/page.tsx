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
  nume_fisier: string;
  clauze_analizate: number;
  clauze_acoperite: number;
  avertisment: string;
  rezultate: ClauzaAnalizata[];
};

export default function Pagina() {
  const [intrebare, setIntrebare] = useState("");
  const [rezultat, setRezultat] = useState<Raspuns | null>(null);
  const [lucreaza, setLucreaza] = useState(false);
  const [eroare, setEroare] = useState("");
  const [stare, setStare] = useState<{ articole: number } | null>(null);
  const [raport, setRaport] = useState<Raport | null>(null);
  const [analizeaza, setAnalizeaza] = useState(false);

  async function incarcaDocument(fisier: File) {
    setAnalizeaza(true);
    setEroare("");
    setRaport(null);
    setRezultat(null);
    const date = new FormData();
    date.append("fisier", fisier);
    try {
      const r = await fetch(`${API}/analizeaza`, { method: "POST", body: date });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail ?? `Serverul a raspuns ${r.status}`);
      setRaport(d);
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
              ? "Analizez documentul, dureaza cateva minute..."
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
              {raport.nume_fisier} — {raport.clauze_analizate} clauze analizate,{" "}
              {raport.clauze_acoperite} cu temei legal identificat.
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
