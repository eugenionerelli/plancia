"""Voce: sintesi, riproduzione e ascolto.

Due motori. Voicebox se il suo backend locale risponde, perché è la voce clonata
e regge bene le lingue. Altrimenti le voci di sistema di macOS, che ci sono
sempre, non chiedono niente e partono in un decimo di secondo.
"""

import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .voce_testo import per_voce

VOICEBOX = "http://127.0.0.1:17493"
AUDIO_DIR = config.DATA_DIR / "audio"

# Voci di sistema preferite per lingua, con il ripiego sulla prima che combacia.
PREFERITE = {
    "it": ["Alice", "Federica", "Luca"],
    "en": ["Samantha", "Alex", "Daniel", "Karen"],
    "es": ["Mónica", "Monica", "Jorge", "Paulina"],
    "fr": ["Thomas", "Amélie", "Amelie"],
    "de": ["Anna", "Markus", "Petra"],
    "pt": ["Luciana", "Joana", "Felipe"],
}

_voci = None


def _play_cmd():
    return ["afplay"]


# --------------------------------------------------------------------------
# voci di sistema
# --------------------------------------------------------------------------

def voci_sistema() -> list:
    global _voci
    if _voci is None:
        _voci = []
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                                 timeout=10).stdout
        except Exception:
            out = ""
        for line in out.splitlines():
            m = re.match(r"^(.+?)\s{2,}([a-z]{2}_[A-Z]{2,3})\s", line)
            if m:
                _voci.append((m.group(1).strip(), m.group(2)))
    return _voci


def voce_per(lang: str) -> str:
    scelta = config.load_config().get("voce_sistema", {})
    if isinstance(scelta, dict) and scelta.get(lang):
        return scelta[lang]
    nomi = {n for n, _ in voci_sistema()}
    for pref in PREFERITE.get(lang, []):
        if pref in nomi:
            return pref
    for nome, loc in voci_sistema():
        if loc.startswith(lang + "_") and "(" not in nome:
            return nome
    return "Alex"


def sintesi_say(testo: str, lang: str, out: Path) -> Path:
    voce = voce_per(lang)
    rate = str(config.load_config().get("velocita_voce", 185))
    subprocess.run(["say", "-v", voce, "-r", rate, "-o", str(out),
                    "--data-format=LEI16@22050", testo],
                   capture_output=True, timeout=180, check=True)
    return out


# --------------------------------------------------------------------------
# Voicebox
# --------------------------------------------------------------------------

def _get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return res.status, res.read()


def _post(url, payload, timeout=20):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8") or "{}")


# Quando Voicebox non risponde ci si ricorda per un po' che non risponde. Se e'
# spento la risposta e' immediata e non serve; se e' piantato, cioe' il processo
# c'e' ma non accetta, ogni frase pagherebbe l'attesa intera. E gli succede: il
# suo server e' morto due volte in un minuto dentro MLX.
_ultimo_no = 0.0
RIPROVA_DOPO = 30


def voicebox_vivo(timeout=1.5) -> bool:
    global _ultimo_no
    if time.time() - _ultimo_no < RIPROVA_DOPO:
        return False
    try:
        code, _ = _get(f"{VOICEBOX}/profiles", timeout=timeout)
        if code == 200:
            _ultimo_no = 0.0
            return True
    except Exception:
        pass
    _ultimo_no = time.time()
    return False


def voicebox_profili() -> list:
    try:
        _, body = _get(f"{VOICEBOX}/profiles")
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, list) else data.get("profiles", [])
    except Exception:
        return []


def voicebox_avvia(attesa=25) -> bool:
    """Apre l'app e aspetta che il backend risponda. Serve solo se richiesto."""
    if voicebox_vivo():
        return True
    try:
        subprocess.run(["open", "-a", "Voicebox"], capture_output=True, timeout=15)
    except Exception:
        return False
    scaduto = time.time() + attesa
    while time.time() < scaduto:
        if voicebox_vivo():
            return True
        time.sleep(1.2)
    return False


def sintesi_voicebox(testo: str, lang: str, out: Path, timeout=180) -> Path:
    cfg = config.load_config()
    profilo = cfg.get("voicebox_profilo")
    if not profilo:
        profili = voicebox_profili()
        if not profili:
            raise RuntimeError("Voicebox non ha profili vocali")
        profilo = profili[0].get("id") or profili[0].get("profile_id")
    res = _post(f"{VOICEBOX}/generate", {
        "profile_id": profilo, "text": testo, "language": lang,
        "engine": cfg.get("voicebox_engine", "qwen"),
    })
    gid = res.get("id") or res.get("generation_id") or res.get("job_id")
    if not gid:
        raise RuntimeError(f"Voicebox non ha restituito un id: {res}")
    # Lo stato arriva come event-stream. Invece di interpretarlo, si aspetta che
    # l'audio esista: è lo stesso segnale e non dipende dal formato degli eventi.
    scaduto = time.time() + timeout
    while time.time() < scaduto:
        try:
            code, body = _get(f"{VOICEBOX}/audio/{gid}", timeout=10)
            if code == 200 and len(body) > 2048:
                out.write_bytes(body)
                return out
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass
        time.sleep(0.8)
    raise TimeoutError("Voicebox non ha prodotto l'audio in tempo")


# --------------------------------------------------------------------------
# interfaccia unica
# --------------------------------------------------------------------------

def motore_scelto() -> str:
    return config.load_config().get("motore_voce", "auto")


# Oltre questo, aspettare non ha senso: si passa alla voce di sistema.
# Misurato sul suo Mac: Voicebox mette 42 secondi anche per una frase corta,
# che vanno bene per il riepilogo della mattina, dove nessuno sta aspettando,
# e non vanno bene per niente in una conversazione.
ATTESA_VOICEBOX = 5


def sintesi(testo: str, lang: str = "it", motore: str = None, cache=True,
            subito=False) -> dict:
    """Il file audio, generato solo se non c'è già.

    `subito` è per quando qualcuno sta aspettando di sentire: si prova la voce
    clonata per pochi secondi e poi si passa a quella di sistema, invece di
    lasciare la persona davanti al silenzio.
    """
    # Ultimo punto prima dell'audio: qui il testo diventa una cosa da dire.
    testo = per_voce(testo, lang)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    motore = motore or motore_scelto()
    chiave = hashlib.sha1(f"{motore}|{lang}|{testo}".encode("utf-8")).hexdigest()[:16]
    out = AUDIO_DIR / f"{chiave}.wav"
    if cache and out.exists() and out.stat().st_size > 2048:
        return {"file": str(out), "motore": "cache", "lingua": lang}

    usato = None
    if motore in ("auto", "voicebox"):
        try:
            if motore == "voicebox" and config.load_config().get("voicebox_avvio_automatico"):
                voicebox_avvia()
            if voicebox_vivo():
                sintesi_voicebox(testo, lang, out,
                                 timeout=ATTESA_VOICEBOX if subito else 180)
                usato = "voicebox"
        except Exception:
            usato = None
    if usato is None:
        if motore == "voicebox":
            raise RuntimeError("Voicebox non risponde su 127.0.0.1:17493")
        sintesi_say(testo, lang, out)
        usato = "say"
    return {"file": str(out), "motore": usato, "lingua": lang}


_riproduzione = None


def riproduci(path, attendi=False):
    global _riproduzione
    ferma()
    _riproduzione = subprocess.Popen(_play_cmd() + [str(path)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if attendi:
        _riproduzione.wait()
    return _riproduzione.pid


def ferma():
    global _riproduzione
    if _riproduzione and _riproduzione.poll() is None:
        _riproduzione.terminate()
    _riproduzione = None


def parla(testo: str, lang: str = "it", motore: str = None, attendi=True) -> dict:
    info = sintesi(testo, lang, motore)
    riproduci(info["file"], attendi=attendi)
    return info


# --------------------------------------------------------------------------
# ascolto
# --------------------------------------------------------------------------

def trascrivi(path, lang: str = "it") -> str:
    """Voicebox se c'è, altrimenti il whisper locale, altrimenti niente."""
    path = str(path)
    if voicebox_vivo():
        try:
            return _multipart_transcribe(path)
        except Exception:
            pass
    for exe in ("whisper-cli", "whisper"):
        try:
            res = subprocess.run([exe, path, "--language", lang, "--output_format", "txt",
                                  "--output_dir", str(AUDIO_DIR)],
                                 capture_output=True, text=True, timeout=300)
            if res.returncode == 0:
                txt = AUDIO_DIR / (Path(path).stem + ".txt")
                if txt.exists():
                    return txt.read_text("utf-8").strip()
        except FileNotFoundError:
            continue
        except Exception:
            break
    return ""


def _multipart_transcribe(path: str) -> str:
    confine = "----plancia" + hashlib.sha1(path.encode()).hexdigest()[:12]
    dati = Path(path).read_bytes()
    corpo = (
        f"--{confine}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{Path(path).name}\"\r\nContent-Type: audio/wav\r\n\r\n"
    ).encode() + dati + (
        f"\r\n--{confine}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nturbo"
        f"\r\n--{confine}--\r\n"
    ).encode()
    req = urllib.request.Request(
        f"{VOICEBOX}/transcribe", data=corpo,
        headers={"Content-Type": f"multipart/form-data; boundary={confine}"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as res:
        data = json.loads(res.read().decode("utf-8"))
    return (data.get("text") or data.get("transcript") or "").strip()


def stato() -> dict:
    cfg = config.load_config()
    return {
        "motore": motore_scelto(),
        "lingua": cfg.get("lingua", "it"),
        "voce_attuale": voce_per(cfg.get("lingua", "it")),
        "voicebox_vivo": voicebox_vivo(),
        "voicebox_installato": Path("/Applications/Voicebox.app").exists(),
        "voci_sistema": len(voci_sistema()),
        "lingue_disponibili": sorted({loc[:2] for _, loc in voci_sistema()}),
    }
