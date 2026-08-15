"""
Teste pentru garantiile deterministe ale produsului.

Aici NU se testeaza calitatea modelului. Se testeaza exact partile care trebuie
sa fie adevarate indiferent ce spune modelul, adica lucrurile pe care se sprijina
promisiunea "nu intoarce nimic fals":

  - citarea afisata provine din baza, nu de la model
  - o trimitere juridica inventata de model opreste raspunsul
  - o trimitere care exista in surse este citat fidel, nu inventie
  - notele nu contamineaza textul normativ
  - diacriticele si flexiunea nu rup cautarea

Fiecare test are in nume comportamentul apărat, nu functia apelata.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer.generate import (  # noqa: E402
    detecteaza_citari_inventate,
    leaga_citari,
)
from app.search.retrieve import (  # noqa: E402
    _fts_query,
    _stem_prefix,
    detecteaza_articol,
)
from app.ingest.parser import (  # noqa: E402
    Articol,
    normalize,
    strip_diacritics,
)
from app.search.retrieve import Rezultat  # noqa: E402


def sursa(numar: str, text: str, citare: str = "") -> Rezultat:
    return Rezultat(
        articol_id=int(numar) if numar.isdigit() else 1,
        numar=numar,
        act_slug="codul-muncii",
        citare=citare or f"Articolul {numar} din CODUL MUNCII",
        cale=f"Titlul I > Articolul {numar}",
        text=text,
        scor=1.0,
        sursa="hibrid",
    )


# --- ISC-3: citarea nu vine de la model ------------------------------------

def test_citarea_afisata_provine_din_baza_nu_din_text_generat():
    surse = [sursa("145", "Durata minima este de 20 de zile lucratoare.")]
    legat, folosite = leaga_citari("Ai minim 20 de zile [S1].", surse)
    assert "Articolul 145 din CODUL MUNCII" in legat
    assert folosite == surse


def test_marcaj_catre_sursa_inexistenta_este_eliminat_nu_inventat():
    surse = [sursa("145", "text")]
    legat, folosite = leaga_citari("Afirmatie [S7].", surse)
    assert "S7" not in legat
    assert folosite == []


def test_trimitere_inventata_de_model_este_semnalata():
    surse = [sursa("145", "Durata minima este de 20 de zile lucratoare.")]
    incalcari = detecteaza_citari_inventate(
        "Conform articolului 300, ai dreptul la concediu.", surse
    )
    assert incalcari, "o trimitere absenta din surse trebuie semnalata"


def test_trimitere_care_exista_in_surse_este_citat_fidel_nu_inventie():
    """Textele de lege contin trimiteri interne. Redarea lor nu e halucinatie."""
    surse = [sursa("75", "Persoanele concediate in temeiul art. 61 lit. c) beneficiaza de preaviz.")]
    incalcari = detecteaza_citari_inventate(
        "Persoanele concediate in temeiul art. 61 au drept la preaviz [S1].", surse
    )
    assert incalcari == []


def test_detectorul_ignora_diferentele_de_punctuatie():
    surse = [sursa("75", "concediate in temeiul art. 61 lit. c)")]
    assert detecteaza_citari_inventate("in temeiul art.61 [S1]", surse) == []


# --- Ingestie: notele nu contamineaza norma --------------------------------

def test_normalizarea_repara_diacriticele_cu_sedila():
    assert normalize("Ministerul Finanţelor") == "Ministerul Finanțelor"
    assert normalize("aşa") == "așa"


def test_normalizarea_elimina_bom_ul_din_sursa():
    assert normalize("﻿ CODUL MUNCII") == "CODUL MUNCII"


def test_normalizarea_este_idempotenta():
    o_data = normalize("﻿  Ministerul Finanţelor ")
    assert normalize(o_data) == o_data


def test_varianta_fara_diacritice_permite_cautare_toleranta():
    assert strip_diacritics("concediu de odihnă") == "concediu de odihna"


# --- Regasire: flexiune si trimiteri explicite -----------------------------

def test_prefixul_acopera_flexiunea_romaneasca():
    """FTS5 nu stemuieste. Fara prefix, 'preavizul' nu gaseste 'preaviz'."""
    assert _stem_prefix("preavizul") == "preavi*"
    assert _stem_prefix("concedierea") == "conced*"


def test_cuvintele_scurte_raman_neschimbate():
    assert _stem_prefix("ore") == "ore"


def test_interogarea_fts_elimina_cuvintele_de_umplutura():
    q = _fts_query("Care este durata concediului de odihna?")
    assert "durata" not in q.lower() or "durat" in q.lower()
    assert '"' not in q  # prefixele nu se pun intre ghilimele
    assert "este" not in q


def test_interogarea_fts_nu_crapa_pe_intrebare_fara_cuvinte_utile():
    assert _fts_query("Ce este?") == '""'


@pytest.mark.parametrize(
    "intrebare,numar,slug",
    [
        ("Ce prevede articolul 145 din Codul muncii?", "145", "codul-muncii"),
        ("Ce prevede articolul 17 din Codul fiscal?", "17", "cod-fiscal"),
        ("Ce prevede articolul 22^1 din Codul fiscal?", "22^1", "cod-fiscal"),
    ],
)
def test_trimiterea_explicita_este_rezolvata_determinist(intrebare, numar, slug):
    rezultat = detecteaza_articol(intrebare)
    assert rezultat is not None
    assert rezultat == (numar, slug)


def test_intrebarea_fara_trimitere_explicita_nu_activeaza_ruta_determinista():
    assert detecteaza_articol("Cate zile de concediu am?") is None


# --- Ierarhie ---------------------------------------------------------------

def test_calea_articolului_include_lantul_de_parinti():
    a = Articol(
        numar="55", eticheta="Articolul 55", denumire="", text="text",
        parinti={"Titlu": "Titlul II Contractul", "Capitol": "Capitolul V Incetarea"},
    )
    assert a.cale() == "Titlul II Contractul > Capitolul V Incetarea > Articolul 55"


def test_calea_functioneaza_si_fara_ierarhie():
    a = Articol(numar="1", eticheta="Articolul 1", denumire="", text="t")
    assert a.cale() == "Articolul 1"


@pytest.mark.parametrize(
    "formulare",
    [
        "Conform articolului 300, ai dreptul la concediu.",
        "Potrivit art. 300 din lege.",
        "Vezi articolele 300 si 301.",
        "In temeiul articolelor 300 de mai sus.",
        "Conform alineatului 4 al aceluiasi text.",
        "Legea 300/2020 prevede altceva.",
    ],
)
def test_toate_formele_flexionare_de_trimitere_sunt_prinse(formulare):
    """Romana flexioneaza. O garda care prinde doar nominativul nu e o garda."""
    surse = [sursa("145", "Durata minima este de 20 de zile lucratoare.")]
    assert detecteaza_citari_inventate(formulare, surse), formulare


def test_numerotarea_surselor_pentru_verificatori_o_urmeaza_pe_cea_a_modelului():
    """Verificatorii trebuie sa vada aceeasi numerotare ca generatorul.

    Daca modelul citeaza doar [S3], iar verificatorului i se dau doar sursele
    citate, acelea se renumeroteaza de la [S1] si marcajul [S3] din raspuns nu
    mai are corespondent. Rezultatul e un refuz fals pe un raspuns corect.
    """
    from app.answer.verify import _extrase

    candidati = [sursa("10", "primul"), sursa("20", "al doilea"), sursa("30", "al treilea")]
    text = _extrase(candidati)
    assert "[S1]" in text and "[S2]" in text and "[S3]" in text
    # al treilea candidat trebuie sa fie [S3], nu [S1]
    pozitie_s3 = text.index("[S3]")
    assert "al treilea" in text[pozitie_s3:]


def test_ordinea_inversata_pastreaza_numerele_originale():
    """Al doilea verificator primeste sursele in ordine inversa, dar cu aceleasi numere."""
    from app.answer.verify import _extrase

    candidati = [sursa("10", "primul"), sursa("20", "al doilea")]
    text = _extrase(candidati, inversat=True)
    assert text.index("[S2]") < text.index("[S1]")  # ordine inversata
    pozitie_s1 = text.index("[S1]")
    assert "primul" in text[pozitie_s1:]  # dar [S1] e tot primul candidat


def test_compactarea_renumeroteaza_marcajele_pe_sursele_citate():
    """Modelul citeaza [S3]; verificatorul trebuie sa primeasca [S1] si sursa 3."""
    from app.answer.generate import compacteaza_marcaje

    candidati = [sursa("10", "primul"), sursa("20", "al doilea"), sursa("30", "al treilea")]
    text, folosite = compacteaza_marcaje("Afirmatie [S3].", candidati)
    assert text == "Afirmatie [S1]."
    assert [f.numar for f in folosite] == ["30"]


def test_compactarea_pastreaza_ordinea_si_mapeaza_corect_mai_multe_surse():
    from app.answer.generate import compacteaza_marcaje

    candidati = [sursa("10", "a"), sursa("20", "b"), sursa("30", "c"), sursa("40", "d")]
    text, folosite = compacteaza_marcaje("Unu [S2]. Doi [S4]. Trei [S2].", candidati)
    assert text == "Unu [S1]. Doi [S2]. Trei [S1]."
    assert [f.numar for f in folosite] == ["20", "40"]


def test_compactarea_elimina_marcajele_catre_surse_inexistente():
    from app.answer.generate import compacteaza_marcaje

    candidati = [sursa("10", "a")]
    text, folosite = compacteaza_marcaje("Afirmatie [S9].", candidati)
    assert "S9" not in text
    assert folosite == []


def test_referirea_corecta_la_articolul_primit_nu_e_inventie():
    """Textul unui articol nu isi repeta propriul numar; acela e in eticheta.

    Bug masurat: 5 din 7 refuzuri false pe o rulare de 30 de cazuri erau
    raspunsuri corecte, respinse fiindca citau corect articolul primit.
    """
    s = sursa("44", "Delegarea poate fi dispusa pentru cel mult 60 de zile.",
              citare="Articolul 44 din CODUL MUNCII")
    assert detecteaza_citari_inventate("Conform articolului 44, delegarea [S1].", [s]) == []
    assert detecteaza_citari_inventate("Vezi art. 44 [S1].", [s]) == []


def test_referirea_la_un_articol_care_nu_a_fost_dat_ramane_inventie():
    s = sursa("44", "Delegarea poate fi dispusa pentru cel mult 60 de zile.",
              citare="Articolul 44 din CODUL MUNCII")
    assert detecteaza_citari_inventate("Conform articolului 999 [S1].", [s])
