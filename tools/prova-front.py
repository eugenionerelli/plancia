#!/usr/bin/env python3
"""Il collaudo del front, senza aprire un browser.

    python3 tools/prova-front.py

Guardare la pagina con un browser vero non si può automatizzare qui: la pagina ha
timer che vanno avanti da soli, e il tempo virtuale di Chrome non arriva mai in
fondo. Quindi si guarda il sorgente, e si cercano le due cose che si sono rotte
davvero: una stringa italiana che nessuno ha tradotto e che quindi compare in
mezzo all'inglese, e una vista che nessuno può raggiungere.
"""

import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
APP = RADICE / "web" / "app.js"
INDEX = RADICE / "web" / "index.html"

# Se una stringa ha una di queste dentro, è italiana e in inglese non ci sta.
ITALIANE = re.compile(
    r"(?:\b(?:il|lo|la|gli|le|un|una|che|per|con|non|del|della|dei|delle|"
    r"sono|hai|nessun|nessuna|ancora|oggi|ieri|adesso|questa|questo)\b|[àèéìòù])",
    re.IGNORECASE)

falliti = []
passati = 0


def prova(nome, condizione, dettaglio=""):
    global passati
    if condizione:
        passati += 1
        print(f"  ok   {nome}")
    else:
        falliti.append(nome)
        print(f"  NO   {nome} {dettaglio}")


def main():
    sorgente = APP.read_text(encoding="utf-8")
    indice = INDEX.read_text(encoding="utf-8")

    # il dizionario inglese: tutte le chiavi che sa tradurre
    inizio = sorgente.index("const EN = {")
    fine = sorgente.index("\n};", inizio)
    dizionario = sorgente[inizio:fine]
    chiavi = set(re.findall(r"'((?:[^'\\]|\\.)*)'\s*:", dizionario))
    chiavi |= set(re.findall(r'"((?:[^"\\]|\\.)*)"\s*:', dizionario))

    # tutte le stringhe passate a T(), che è quello che l'utente legge
    usate = set(re.findall(r"T\('((?:[^'\\]|\\.)*)'\)", sorgente))
    usate |= set(re.findall(r'T\("((?:[^"\\]|\\.)*)"\)', sorgente))
    prova("il front chiama T su qualcosa", len(usate) > 40, f"trovate {len(usate)}")

    orfane = sorted(s for s in usate if s not in chiavi and ITALIANE.search(s))
    prova("nessuna frase italiana senza traduzione", not orfane,
          f"{len(orfane)}: {orfane[:6]}")

    # le viste dichiarate e quelle raggiungibili dal menu
    viste = set(re.findall(r"views\.(\w+)\s*=", sorgente))
    prova("le viste ci sono tutte",
          {"oggi", "lavagna", "progetti", "social", "archivio", "benvenuto"} <= viste,
          str(sorted(viste)))

    nel_menu = set(re.findall(r'href="#/(\w+)"', indice))
    orfane_viste = sorted(v for v in viste
                          if v not in nel_menu and v not in ("benvenuto", "cerca"))
    prova("ogni vista si raggiunge dal menu", not orfane_viste, str(orfane_viste))

    # i data-act usati nel markup devono avere un pezzo di codice che li ascolta
    azioni = set(re.findall(r"data-act=\"(\w[\w-]*)\"", sorgente))
    ascoltate = set(re.findall(r"name === '([\w-]+)'", sorgente))
    sorde = sorted(a for a in azioni if a not in ascoltate)
    prova("ogni bottone ha qualcuno che lo ascolta", not sorde, str(sorde))

    prova("niente template letterali dentro le stringhe tradotte",
          not [s for s in usate if "${" in s])

    print()
    print(f"{passati} passate, {len(falliti)} fallite")
    if falliti:
        print("fallite: " + ", ".join(falliti))
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
