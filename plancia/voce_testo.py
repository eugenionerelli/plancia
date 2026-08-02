"""Il testo, ripulito per essere detto invece che letto.

Un indirizzo web letto ad alta voce diventa "acca ti ti pi due punti barra
barra", un percorso di file una filastrocca di cartelle, e uno sha di commit
quaranta lettere a caso. Sono le tre cose che fanno spegnere una voce sintetica
dopo due giorni.

Qui non si riassume e non si cambia il senso: si tolgono solo i pezzi che
esistono per gli occhi. Chi vuole il testo intero lo ha lo stesso, perché queste
funzioni si usano solo sulla strada della voce.
"""

import re

# Il dominio nudo va riconosciuto anche senza http davanti: quasi nessuno lo
# scrive. E il percorso deve prendersi anche la tilde, se no resta lì da sola.
URL = re.compile(r"https?://\S+|\b(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/\S*)+")
PERCORSO = re.compile(r"~?(?:/[\w.\-]+){2,}")
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
MARCATORI = re.compile(r"[`*_#>]+")

DOMINI = {
    "github.com": {"it": "su GitHub", "en": "on GitHub", "es": "en GitHub"},
    "x.com": {"it": "su X", "en": "on X", "es": "en X"},
    "twitter.com": {"it": "su X", "en": "on X", "es": "en X"},
}

GENERICO = {"it": "a un indirizzo web", "en": "at a link", "es": "en un enlace"}


def _dominio(url: str, lang: str) -> str:
    pulito = re.sub(r"^https?://", "", url).lower()
    for dominio, dette in DOMINI.items():
        if pulito.startswith(dominio) or pulito.startswith("www." + dominio):
            return dette.get(lang, dette["en"])
    return GENERICO.get(lang, GENERICO["en"])


# Le preposizioni che finiscono raddoppiate quando l'indirizzo diventa "su
# GitHub": "pubblicato su su GitHub" non lo direbbe nessuno.
DOPPIE = re.compile(r"\b(su|on|en|at|in)\s+\1\b", re.IGNORECASE)


def per_voce(testo: str, lang: str = "it") -> str:
    """Il testo come lo diresti, senza le cose che si scrivono e basta."""
    if not testo:
        return testo

    def _url(m):
        grezzo = m.group(0)
        # la punteggiatura attaccata in fondo è della frase, non dell'indirizzo
        coda = ""
        while grezzo and grezzo[-1] in ".,;:!?)":
            coda = grezzo[-1] + coda
            grezzo = grezzo[:-1]
        return _dominio(grezzo, lang) + coda

    testo = URL.sub(_url, testo)
    # Di un percorso interessa l'ultimo pezzo: è quello che una persona direbbe.
    testo = PERCORSO.sub(lambda m: m.group(0).rstrip("/").split("/")[-1], testo)
    # Uno sha non si dice: la frase intorno dice già di quale commit si parla.
    testo = SHA.sub("", testo)
    testo = MARCATORI.sub("", testo)

    # gli spazi e la punteggiatura rimasti orfani
    testo = re.sub(r"\(\s*\)", "", testo)
    testo = re.sub(r"\s+([,.;:!?])", r"\1", testo)
    testo = re.sub(r"([,.;:])\1+", r"\1", testo)
    testo = re.sub(r"\s{2,}", " ", testo)
    testo = DOPPIE.sub(lambda m: m.group(1), testo)
    return testo.strip()
