"""Il registro: cosa è successo, in un formato che possono leggere anche gli altri.

Plancia sa quando un lavoro parte, quando finisce, quando un task si chiude,
quando un post esce. Queste cose servono anche fuori: all'agente che scrive i
post serve sapere cosa è stato spedito, a una scorciatoia serve sapere quando un
lancio è finito.

Invece di inventare una API per ogni consumatore, c'è un file solo, in append,
una riga per evento, con uno schema dichiarato. Chi legge tiene un segnalibro e
riparte da lì. È il formato più noioso possibile, ed è il motivo per cui
funzionerà anche fra due anni.

    ~/.plancia/eventi.jsonl

Ogni riga:

    {"schema":"plancia.evento/1","id":"...","ts":"...Z","tipo":"lavoro.completato",
     "titolo":"...","progetto":"scriba","origine":"cantiere","dati":{...}}

Il file non viene mai riscritto, solo accodato, e viene ruotato quando supera i
cinque megabyte. Le vecchie righe finiscono in `eventi.1.jsonl`.
"""

import json
import os
import secrets
import threading

from . import config, store

SCHEMA = "plancia.evento/1"
FILE = config.DATA_DIR / "eventi.jsonl"
MAX_BYTE = 5 * 1024 * 1024

# I tipi che Plancia emette. Chi legge dovrebbe ignorare quelli che non conosce
# invece di rompersi: l'elenco crescerà.
TIPI = (
    "lavoro.avviato",       # un agente è partito su un task
    "lavoro.completato",    # ha finito bene
    "lavoro.fallito",       # ha finito male
    "task.creato",
    "task.chiuso",
    "post.pubblicato",
    "progetto.archiviato",
    "progetto.aggiornato",
    "riepilogo.pronto",
)

_lucchetto = threading.Lock()


def scrivi(tipo: str, titolo: str = "", progetto=None, dati=None, origine="plancia") -> dict:
    """Accoda un evento. Non solleva mai: il registro non deve poter rompere
    il lavoro che sta registrando."""
    evento = {
        "schema": SCHEMA,
        "id": secrets.token_hex(8),
        "ts": store.now(),
        "tipo": tipo,
        "titolo": titolo or "",
        "progetto": progetto if isinstance(progetto, str) else None,
        "origine": origine,
        "dati": dati or {},
    }
    try:
        config.ensure_dirs()
        with _lucchetto:
            _ruota()
            with open(FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return evento


def _ruota():
    try:
        if FILE.exists() and FILE.stat().st_size > MAX_BYTE:
            FILE.replace(FILE.with_suffix(".1.jsonl"))
    except Exception:
        pass


def leggi(dopo: str = None, tipo: str = None, limite: int = 100) -> list:
    """Gli eventi dopo un certo id, in ordine. Senza `dopo` torna gli ultimi.

    `dopo` è l'id dell'ultimo evento già visto: è il segnalibro del consumatore.
    """
    righe = []
    for path in (FILE.with_suffix(".1.jsonl"), FILE):
        try:
            with open(path, encoding="utf-8") as fh:
                for riga in fh:
                    try:
                        righe.append(json.loads(riga))
                    except Exception:
                        continue
        except OSError:
            continue

    if dopo:
        for i, e in enumerate(righe):
            if e.get("id") == dopo:
                righe = righe[i + 1:]
                break
    if tipo:
        voluti = {t.strip() for t in tipo.split(",") if t.strip()}
        righe = [e for e in righe if e.get("tipo") in voluti]
    if not dopo:
        righe = righe[-limite:]
    return righe[:limite]


def ultimo_id() -> str:
    righe = leggi(limite=1)
    return righe[-1]["id"] if righe else ""


def stato() -> dict:
    n = 0
    try:
        with open(FILE, encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
    except OSError:
        pass
    return {"file": str(FILE), "schema": SCHEMA, "eventi": n,
            "byte": FILE.stat().st_size if FILE.exists() else 0,
            "tipi": list(TIPI)}
