"""
Registrul de joburi de analiza.

Testele apara mai ales CAILE DE ESEC. Un registru care pierde un rezultat sau
lasa un job vesnic "in lucru" produce un client care interogheaza la nesfarsit
o analiza moarta, si asta arata identic cu un produs stricat.
"""

import threading
import time

from app.documente.joburi import ESUAT, GATA, IN_LUCRU, Registru


def _asteapta(registru, job_id, stare_dorita, secunde=5.0):
    limita = time.monotonic() + secunde
    while time.monotonic() < limita:
        job = registru.ia(job_id)
        if job and job.stare == stare_dorita:
            return job
        time.sleep(0.01)
    raise AssertionError(f"jobul a ramas in {registru.ia(job_id).stare}")


def test_jobul_trece_prin_stari_si_aduna_rezultate():
    r = Registru()
    job = r.creeaza("contract.pdf", 4200, 3)
    assert job.stare == IN_LUCRU and job.clauze_gata == 0

    r.porneste(job.id, lambda: [r.adauga_rezultat(job.id, i) for i in range(3)])
    gata = _asteapta(r, job.id, GATA)
    assert gata.rezultate == [0, 1, 2]
    assert gata.clauze_gata == 3


def test_rezultatele_sunt_vizibile_inainte_de_final():
    """Progresul incremental e chiar motivul pentru care exista modulul.

    Daca rezultatele ar aparea toate la sfarsit, clientul nu ar putea distinge
    o analiza lenta de una blocata - exact problema de rezolvat.
    """
    r = Registru()
    job = r.creeaza("d.txt", 100, 2)
    poarta = threading.Event()

    def _lucru():
        r.adauga_rezultat(job.id, "prima")
        poarta.wait(5)
        r.adauga_rezultat(job.id, "a doua")

    r.porneste(job.id, _lucru)
    limita = time.monotonic() + 5
    while r.ia(job.id).clauze_gata < 1 and time.monotonic() < limita:
        time.sleep(0.01)

    partial = r.ia(job.id)
    assert partial.clauze_gata == 1, "primul rezultat trebuie vizibil inainte de final"
    assert partial.stare == IN_LUCRU
    poarta.set()
    _asteapta(r, job.id, GATA)


def test_o_exceptie_marcheaza_jobul_esuat_nu_il_lasa_in_lucru():
    """Fara asta, exceptia moare tacut in executor si jobul ramane vesnic in lucru."""
    r = Registru()
    job = r.creeaza("d.txt", 10, 1)

    def _crapa():
        raise ValueError("modelul a cazut")

    r.porneste(job.id, _crapa)
    esuat = _asteapta(r, job.id, ESUAT)
    assert "ValueError" in esuat.eroare and "modelul a cazut" in esuat.eroare


def test_rezultatele_de_dinainte_de_eroare_se_pastreaza():
    """O analiza cazuta la clauza a treia tot livreaza primele doua."""
    r = Registru()
    job = r.creeaza("d.txt", 10, 3)

    def _crapa_la_final():
        r.adauga_rezultat(job.id, "a")
        r.adauga_rezultat(job.id, "b")
        raise RuntimeError("gata")

    r.porneste(job.id, _crapa_la_final)
    esuat = _asteapta(r, job.id, ESUAT)
    assert esuat.rezultate == ["a", "b"]


def test_registrul_arunca_joburile_vechi_dar_nu_pe_cele_in_lucru():
    """Plafonul apara memoria, dar nu are voie sa rupa o analiza care ruleaza."""
    r = Registru(max_joburi=3)
    viu = r.creeaza("in-lucru.txt", 10, 1)  # ramane IN_LUCRU, nu il pornim
    for i in range(6):
        vechi = r.creeaza(f"vechi{i}.txt", 10, 1)
        r.incheie(vechi.id)

    assert r.ia(viu.id) is not None, "un job in lucru nu se arunca niciodata"
    assert len(r._joburi) <= 4  # plafonul plus jobul viu care nu poate fi aruncat


def test_job_inexistent_intoarce_none():
    assert Registru().ia("nu-exista") is None


def test_scrierile_concurente_nu_pierd_rezultate():
    """Firul de analiza si interogarile de stare ating acelasi dictionar."""
    r = Registru()
    job = r.creeaza("d.txt", 10, 200)

    def _scrie(start):
        for i in range(start, start + 100):
            r.adauga_rezultat(job.id, i)

    fire = [threading.Thread(target=_scrie, args=(s,)) for s in (0, 100)]
    for f in fire:
        f.start()
    for f in fire:
        f.join()

    assert r.ia(job.id).clauze_gata == 200


def test_paznicul_declara_esuat_un_job_care_nu_se_mai_termina():
    """Cazul care a motivat paznicul, reprodus.

    Un apel HTTP catre serverul de inferenta poate sa NU expire niciodata:
    timeout-ul de citire din httpx e per operatie, nu termen limita. Masurat cu
    Ollama blocat, o conexiune a stat deschisa peste opt minute cu timeout de
    180 de secunde, iar jobul a ramas vesnic "in lucru".
    """
    r = Registru(termen_s=0.3)
    job = r.creeaza("d.txt", 10, 2)
    blocaj = threading.Event()

    def _atarna():
        r.adauga_rezultat(job.id, "prima clauza, terminata inainte de blocaj")
        blocaj.wait(10)  # simuleaza apelul care nu se mai intoarce

    r.porneste(job.id, _atarna)
    esuat = _asteapta(r, job.id, ESUAT, secunde=3)
    assert "depasit" in esuat.eroare
    assert esuat.rezultate == ["prima clauza, terminata inainte de blocaj"], \
        "ce s-a apucat sa se analizeze trebuie livrat, nu aruncat"
    blocaj.set()


def test_paznicul_nu_suprascrie_un_job_terminat_cu_bine():
    """Cronometrul nu are voie sa strice un rezultat bun sosit la timp."""
    r = Registru(termen_s=0.3)
    job = r.creeaza("d.txt", 10, 1)
    r.porneste(job.id, lambda: r.adauga_rezultat(job.id, "gata la timp"))
    _asteapta(r, job.id, GATA, secunde=2)
    time.sleep(0.6)  # trecem de termen
    final = r.ia(job.id)
    assert final.stare == GATA and final.eroare == ""
