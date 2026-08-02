# Cosa è cambiato, e perché

Onboarding scritto, da leggere una volta. Le stesse cose l'app te le fa provare
al primo avvio, e restano sotto "Guida".

## La riga corta

Prima Plancia era un archivio che guardavi. Adesso è un posto da cui lavori: c'è
una lavagna con tutto quello che è aperto, di tutti gli agenti; da ogni riga puoi
mandare il lavoro a Claude o a Codex; il riepilogo finisce con la cosa che
conviene fare, e a voce dici "fallo".

## La lavagna

`#/lavagna` nell'app, `plancia lavagna` da terminale.

Claude Code tiene la sua lista di task in `~/.claude/tasks/<sessione>/`, Codex i
suoi obiettivi in `~/.codex/goals_1.sqlite`, Plancia i suoi nel database. Nessuno
dei tre sapeva degli altri due. La lavagna li legge tutti e riporta gli stati agli
stessi cinque: aperto, in corso, bloccato, fatto, sparito.

Due cose da sapere:

- **Le voci che una fonte non riporta più spariscono.** Se cancelli un task in
  Claude Code non resta lì a fare finta di esistere.
- **I filtri in alto sono due assi diversi**: a sinistra la fonte, a destra lo
  stato. "aperti" comprende anche quelli in corso e bloccati, perché sono tutti
  lavoro che non è finito.

## Mandare un lavoro a un agente

Dal bottone "Manda a un agente", o da `manda` su una qualsiasi riga.

Si apre un compositore: il lavoro, come lo vuoi fatto, su quale progetto, a quale
agente, in che modo. Il campo "come lo voglio fatto" è quello che conta: finisce
nel prompt insieme al contesto del progetto, quindi è il posto dove scrivere
"senza riscrivere il modulo" o "prima dimmi cosa cambieresti".

**Il modo predefinito è `proposta`.** L'agente legge, ragiona e ti riferisce, e
non tocca nessun file. `esegui` lo lascia scrivere, e va scelto ogni volta: il
riquadro diventa rosso quando lo scegli, apposta.

Il task si chiude da solo solo se il lancio era in `esegui` ed è andato bene. Un
lancio in proposta non chiude niente, perché non ha fatto niente.

Da terminale:

```bash
plancia manda "rilancia l'ablation con lo split grande" --agente codex --progetto atlas
```

E per vedere com'è andata:

```bash
plancia lanci
```

## Il riepilogo che finisce con una decisione

Il riepilogo non si ferma più ai fatti. Dopo il racconto della giornata arrivano
le proposte, in ordine di urgenza, ognuna con l'azione già pronta.

Le proposte nascono **solo da segnali che stanno nei dati**:

| segnale | proposta |
|---|---|
| un lancio fallito nelle ultime 72 ore | lo riprovo? |
| un obiettivo di Codex bloccato o senza quota | ci mando Claude a vedere? |
| file modificati e non committati da ieri | guardo cosa sono? |
| un post approvato e mai uscito | lo pubblichiamo? |
| un task in corso da più di tre giorni | lo riprendo? |
| il prossimo passo di un progetto fermo da due giorni | lo mando a un agente? |
| oggi hai generato 2,5 volte la tua media di trenta giorni | tienila d'occhio |

Se il segnale non c'è, la proposta non c'è. Una giornata tranquilla ti dà un
riepilogo corto invece di un consiglio inventato: è voluto.

A voce funzionano "fallo", "la seconda", "eseguilo". "Eseguilo" è l'unico modo
per far partire una proposta in modo esecutivo: "fallo" tiene il modo che la
proposta aveva, e quasi sempre è proposta.

## La voce

Quello che è cambiato sotto:

- **Il microfono non si spegne fra un turno e l'altro.** Prima si fermava e
  riavviava a ogni frase, e la frase dopo perdeva le prime sillabe.
- **Puoi interromperlo.** Ricomincia a parlare mentre risponde e si zittisce. È
  la cancellazione dell'eco sul nodo di ingresso a renderlo possibile: senza, si
  sentirebbe da solo e si interromperebbe da solo.
- **I nomi dei tuoi progetti** vanno al riconoscitore come vocabolario. Senza,
  "Voicebox" diventava "voice books".
- **L'attesa del silenzio si adatta**: più corta sui comandi, più lunga quando
  stai ragionando ad alta voce.
- **Gli errori si sentono.** A mani libere un messaggio scritto non esiste.
- **"Annulla" ferma un lavoro partito**, "basta" chiude il pannello.
- **Quando un lancio finisce te lo dice**, anche se nel frattempo stavi facendo
  altro. Un agente che lavora tre minuti in silenzio ti riporta a guardare lo
  schermo, che è esattamente quello da evitare.
- **Dopo tre minuti che non parli il microfono si chiude da solo.** Un programma
  che si vanta di non mandare niente fuori non può tenerlo aperto tutto il
  giorno perché ti sei alzato senza dire niente. Tocca l'orbita per riprendere.

### La prima volta

macOS chiede microfono e dettatura. Se la finestra non compare, la voce di menu
"Attiva la voce…" nella barra dei menu la fa comparire davvero: la richiesta
partita dal pannello vocale spesso non mostra niente, perché quel pannello è
fatto apposta per non attivare l'app e il sistema mostra quelle finestre solo a
un'app davanti.

Se ricompili spesso, `./tools/certificato.sh` ti fa un certificato di firma
stabile: senza, l'identità dell'app cambia a ogni compilazione e il consenso
viene chiesto di nuovo ogni volta.

## Quanto costa ogni progetto

Ogni scheda di progetto dice quanti token ha generato negli ultimi trenta
giorni. Non sono euro: sono la cosa che finisce davvero, perché i limiti si
prendono lì.

In Oggi il numero grande dei token diventa ambra quando la giornata è sopra due
volte e mezzo la tua media di trenta giorni, e dice di quanto. Prima quel
segnale esisteva solo dentro una proposta, quindi lo vedevi solo se leggevi il
riepilogo fino in fondo.

## Il registro degli eventi

`~/.plancia/eventi.jsonl`, una riga JSON per evento, schema `plancia.evento/1`,
solo in coda, ruota a 5 MB.

```json
{"schema":"plancia.evento/1","id":"9f2c…","ts":"2026-08-02T09:14:22Z",
 "tipo":"lavoro.completato","titolo":"Rilancia l'ablation","progetto":"atlas",
 "origine":"cantiere","dati":{"agente":"codex","modo":"esegui","token":22800}}
```

Serve a far sapere agli altri strumenti cosa è successo senza farli interrogare
il database. Chi legge tiene l'id dell'ultimo evento visto e chiede quello che è
venuto dopo:

```bash
plancia eventi --dopo <id>
```

Per gli agenti social il tipo che conta è `lavoro.completato`: da lì esce il
materiale vero per un post, con il progetto già attaccato.

## I venti tool MCP

Ai sedici di prima si aggiungono `plancia_lavagna`, `plancia_manda`,
`plancia_lanci`, `plancia_eventi`. Li vedono sia Claude Code sia Codex.

La skill `plancia` dice a Claude quando usarli, e una regola in particolare:
`plancia_manda` va in proposta se non gli hai detto tu di eseguire.

## Il sito e il prezzo

Il sito sta in `site/` e si pubblica da solo su GitHub Pages a ogni push:
**https://eugenionerelli.github.io/plancia/**

Il modello: sorgente completo e gratuito sotto GPL, build firmata a pagamento
(quanto vuoi, da 5 euro). È quello di Ardour e Krita. Quello che si paga è il
certificato Apple, la notarizzazione e la manutenzione, non il software.

Niente chiavi di licenza e niente attivazione: sotto GPL sarebbero una
restrizione ulteriore, vietata dal paragrafo 10. Chi compra può anche
ridistribuire il binario gratis, ed è parte del patto.

I numeri verificati stanno in [MONETIZZAZIONE.md](MONETIZZAZIONE.md), la
procedura in [RILASCIO.md](RILASCIO.md).

## Due bug che ti riguardavano

**Il riepilogo mostrava sempre l'attesa.** Il testo in cache veniva letto prima
di essere stato recuperato, quindi si dipingeva "preparo il riepilogo" e il testo
compariva solo al giro dopo. Adesso appare subito.

**L'interfaccia in inglese parlava italiano a metà**: le domande suggerite e le
proposte seguivano la lingua della configurazione invece di quella scelta con il
selettore.

## Il collaudo

```bash
python3 tools/prova.py
```

Quarantuno controlli in dieci secondi, su un archivio finto che non tocca il tuo.
Non è una suite completa: è la lista delle cose che si sono rotte almeno una
volta. Da far girare prima di ogni release.
