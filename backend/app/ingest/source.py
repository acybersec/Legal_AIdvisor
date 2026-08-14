"""
Descoperirea si descarcarea actelor de pe legislatie.just.ro.

Impartirea sarcinilor, verificata live 14 august 2026:
  - API-ul SOAP  -> DESCOPERIRE. Da metadate: titlu, TipAct, Emitent,
    DataVigoare, LinkHtml. Textul lui e la data republicarii, NU consolidat.
  - Pagina HTML  -> TEXT. Forma consolidata la zi.

Capcane confirmate, vezi API-LEGISLATIE.md:
  - fara User-Agent de browser primesti 403
  - endpointul e .svc/SOAP, nu .svc
  - ordinea campurilor din SearchModel conteaza, iar NumarPagina si
    RezultatePagina sunt obligatorii
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

BASE = "https://legislatie.just.ro"
SOAP_ENDPOINT = f"{BASE}/apiws/FreeWebService.svc/SOAP"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
NS_ACTION = "http://tempuri.org/IFreeWebService"

_TOKEN_ENVELOPE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
    '<GetToken xmlns="http://tempuri.org/"/>'
    "</s:Body></s:Envelope>"
)


@dataclass
class ActMeta:
    titlu: str
    tip_act: str
    numar: str
    emitent: str
    data_vigoare: str
    link_html: str
    doc_id: str  # extras din LinkHtml, identificatorul stabil al actului


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA},
        timeout=httpx.Timeout(120.0, connect=30.0),
        follow_redirects=True,
    )


def get_token(client: httpx.Client) -> str:
    r = client.post(
        SOAP_ENDPOINT,
        content=_TOKEN_ENVELOPE,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{NS_ACTION}/GetToken"',
        },
    )
    r.raise_for_status()
    m = re.search(r"<GetTokenResult>([^<]+)</GetTokenResult>", r.text)
    if not m:
        raise RuntimeError(f"GetToken nu a intors token: {r.text[:200]}")
    return m.group(1)


def search(
    client: httpx.Client,
    token: str,
    *,
    titlu: str = "",
    numar: str = "",
    an: str = "",
    text: str = "",
    pagina: int = 1,
    per_pagina: int = 10,
) -> list[ActMeta]:
    """Cauta acte. Ordinea campurilor e impusa de contractul WCF."""
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        '<Search xmlns="http://tempuri.org/">'
        '<SearchModel xmlns:d="http://schemas.datacontract.org/2004/07/FreeWebService">'
        f"<d:NumarPagina>{pagina}</d:NumarPagina>"
        f"<d:RezultatePagina>{per_pagina}</d:RezultatePagina>"
        f"<d:SearchAn>{an}</d:SearchAn>"
        f"<d:SearchNumar>{numar}</d:SearchNumar>"
        f"<d:SearchText>{text}</d:SearchText>"
        f"<d:SearchTitlu>{titlu}</d:SearchTitlu>"
        "</SearchModel>"
        f"<tokenKey>{token}</tokenKey>"
        "</Search></s:Body></s:Envelope>"
    )
    r = client.post(
        SOAP_ENDPOINT,
        content=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{NS_ACTION}/Search"',
        },
    )
    r.raise_for_status()
    if "<s:Fault>" in r.text[:500]:
        fault = re.search(r"<faultstring[^>]*>([^<]{0,200})", r.text)
        raise RuntimeError(f"SOAP Fault: {fault.group(1) if fault else '?'}")

    out: list[ActMeta] = []
    for block in re.findall(r"<a:Legi>(.*?)</a:Legi>", r.text, re.S):
        get = lambda tag: (  # noqa: E731
            (re.search(rf"<a:{tag}>(.*?)</a:{tag}>", block, re.S) or [None, ""])[1] or ""
        ).strip()
        link = get("LinkHtml")
        doc_id = (re.search(r"/(\d+)/?$", link) or [None, ""])[1]
        out.append(
            ActMeta(
                titlu=re.sub(r"\s+", " ", get("Titlu"))[:300],
                tip_act=get("TipAct"),
                numar=get("Numar"),
                emitent=get("Emitent"),
                data_vigoare=get("DataVigoare"),
                link_html=link,
                doc_id=doc_id,
            )
        )
    return out


def fetch_html(client: httpx.Client, doc_id: str, *, politeness_s: float = 1.5) -> str:
    """Descarca pagina consolidata a unui act. Politicos, cu pauza intre cereri."""
    time.sleep(politeness_s)
    url = f"{BASE}/Public/DetaliiDocument/{doc_id}"
    r = client.get(url)
    r.raise_for_status()
    return r.text
