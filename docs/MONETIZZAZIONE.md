# Vendere Plancia restando open source

Verificato il 2 agosto 2026 sulle fonti primarie. Ogni numero qui sotto viene
dalla pagina ufficiale di chi lo pratica, non da un riassunto di terze parti. Le
condizioni cambiano: prima di firmare qualcosa, ricontrolla.

Non è consulenza fiscale. È solo quello che ciascuna piattaforma dichiara.

## La domanda vera

Non "come faccio a impedire che me lo copino", ma "come incasso da tutto il mondo
senza aprire una partita IVA in ventisette paesi".

Chi vende software a un consumatore europeo deve l'IVA nel paese del compratore.
Farlo da soli significa registrarsi al regime OSS e presentare dichiarazioni
trimestrali. La via d'uscita è vendere attraverso un **merchant of record**: la
piattaforma è il venditore verso il cliente, si prende addosso l'IVA, e a te gira
il netto.

## Le quattro piattaforme

| | commissione | individui | minimo di pagamento | note |
|---|---|---|---|---|
| **Polar** | 5% + 0,50 $ (+1,5% carte estere) | **sì, Spagna e Italia dichiarate** | nessuno dichiarato | chiavi di licenza e download come funzioni native |
| Paddle | 5% + 0,50 $ | sì, ma verifica identità con documento | 100 € | prodotti sotto i 10 $ solo parlando con le vendite |
| Lemon Squeezy | 5% + 0,50 $ (+1,5% estero, +1% payout non USA) | sì | 50 $ | vivo ma fermo: nessuna funzione nuova da agosto 2025, la registrazione ora smista verso Stripe |
| Gumroad | 10% + 0,50 $ **più** 2,9% + 0,30 $ di carta, cioè ~12,9% | sì | 100 $ | il "10% tutto compreso" non è tutto compreso |

**La scelta è Polar.** È l'unica delle quattro che mette per iscritto che una
persona fisica può vendere, nominando Spagna e Italia, e l'unica che tratta la
consegna di un file e la chiave di licenza come funzioni di prima classe invece
che come un innesto.

Due cose da sapere prima di aprire l'account:
- il conto va aperto sapendo che le organizzazioni create dal 27 maggio 2026 in
  poi stanno a 5% + 0,50 $; la vecchia tariffa al 4% è solo per chi c'era prima
- la verifica dell'identità passa da Stripe e può prendere fino a due settimane,
  quindi non farla il giorno prima di pubblicare

**Stripe Managed Payments** è la cosa nuova da tenere d'occhio: merchant of
record di Stripe, disponibile a tutti da fine aprile 2026, 3,5% più le
commissioni Stripe normali. Sulla carta costa meno di Polar sopra una certa
soglia di fatturato. Vale la pena rifare il conto quando le vendite ci saranno
davvero, non adesso.

**GitHub Sponsors** va acceso comunque, in parallelo: costa zero sulle donazioni
da account personali (la commissione fino al 6% la pagano solo le organizzazioni
che sponsorizzano), Spagna e Italia sono entrambe supportate, non serve una
società. Minimo di pagamento 100 $ al mese, primo bonifico dopo sessanta giorni,
serve il modulo W-8BEN e la verifica in due fattori. È il canale per chi vuole
lasciare qualcosa senza scaricare niente.

## Quanto costa Apple

Il programma sviluppatori costa **99 dollari l'anno** e non esiste una strada
gratuita: la notarizzazione richiede un certificato **Developer ID**, che si
ottiene solo con l'iscrizione a pagamento. Il "personal team" gratuito rilascia
un certificato *Apple Development*, che serve per le macchine tue e non passa
Gatekeeper altrove.

Su Apple Silicon un binario arm64 non parte proprio senza almeno una firma ad
hoc, che gli strumenti di compilazione mettono da soli: per questo la build
compilata in casa funziona sulla tua macchina. Firma ad hoc e Developer ID sono
due cose diverse, e Gatekeeper riconosce solo la seconda.

## Cosa succede a chi scarica un'app non firmata

Da macOS Sequoia in poi, e quindi anche su Tahoe 26, **il clic destro > Apri non
esiste più**. Apple lo ha annunciato ad agosto 2024: chi vuole aprire software
non firmato deve passare da Impostazioni di Sistema > Privacy e sicurezza >
Sicurezza > Apri comunque, e confermare con la password.

La prima finestra che vede dice che Apple non può verificare l'app e offre solo
"Sposta nel cestino" o "Fine". Non c'è un bottone per aprire. Chi non sa cosa
sta cercando si ferma lì.

Questo è il motivo per cui la build firmata vale dei soldi, ed è la frase da
usare sul sito senza girarci intorno.

## Homebrew si chiude il 1 settembre 2026

Confermato dal manutentore, non è una voce: Homebrew smette di supportare tutti i
cask che non passano Gatekeeper dal 1 settembre 2026, e deprecano insieme i flag
`--no-quarantine` e `--quarantine`. Su 7624 cask, 387 sono già deprecati per
questo.

Due conseguenze pratiche:
- senza notarizzazione, `brew install --cask plancia` non sarà mai possibile in
  `Homebrew/cask`
- **un tap tuo resta libero**: sui tap di terzi Homebrew non controlla neanche lo
  stato della firma. Se vuoi un `brew tap eugenionerelli/plancia` che installa la
  build compilata dal sorgente, quella strada resta aperta anche senza
  certificato

I cask a pagamento sono ammessi. Le regole: una prova a tempo va bene solo se lo
stesso file si attiva come versione completa senza riscaricare niente, e una
versione gratuita che funziona per sempre con funzioni a pagamento opzionali è
ammessa.

## Cosa dice davvero la GPL sul prezzo

Il paragrafo 4 della GPLv3, testuale: puoi far pagare qualsiasi prezzo, o
nessuno, per ogni copia che distribuisci. La FAQ della FSF è ancora più esplicita:
il diritto di vendere copie fa parte della definizione di software libero, e non
c'è un limite al prezzo.

Tre precisazioni che quasi tutti sbagliano:

1. **L'obbligo di dare il sorgente vale verso chi riceve il binario, non verso il
   mondo.** Non sei tenuto a pubblicarlo per tutti. Nel nostro caso lo pubblichi
   comunque, il che semplifica tutto.
2. **Il tetto al "costo ragionevole" non riguarda il prezzo del binario.** Vale
   solo per la strada del paragrafo 6(b), quella del supporto fisico e
   dell'offerta scritta. Noi stiamo sul 6(d): binario e sorgente offerti dallo
   stesso posto, il sorgente senza costi aggiuntivi.
3. **Chi compra può ripubblicare gratis il binario, e può rivenderlo.** Il
   paragrafo 10 vieta di imporre restrizioni ulteriori o di chiedere una royalty
   a chi riceve una copia da qualcun altro. Quindi: **niente chiavi di licenza e
   niente attivazione**, sarebbero incompatibili con la licenza che hai scelto.

Questo non è un buco da tappare, è il patto. Nella pratica non ha fermato nessuno
dei progetti che campano così.

## I precedenti veri

**Ardour** (GPLv2+, non v3). Testuale: chi vuole la comodità della versione già
pronta o il supporto degli sviluppatori paga qualcosa, chi non vuole si compila
il sorgente e lì non danno assistenza. Minimo 1 dollaro, suggerito 30,
abbonamenti da 1 a 50 dollari al mese. Oggi sono 5.715 abbonati per 14.130
dollari al mese. E lo dicono chiaro: niente chiavi di licenza, andrebbero contro
la GPL.

**Krita** (GPLv3). Sorgente libero, build a pagamento su Microsoft Store, Steam,
Epic e Mac App Store, con la stessa frase: paghi la comodità e per aiutare il
progetto, non ci sono differenze funzionali.

**Zrythm** (AGPLv3) è l'analogo più vicino: vende gli installer già pronti a
18/31/50 dollari con il sorgente pubblico.

**Aseprite non è un precedente e non va citato.** Non è open source: dal
settembre 2016 ha abbandonato la GPLv2 per una EULA propria che vieta la
redistribuzione. L'hanno fatto proprio per impedire quello che la GPL permette.
Citarlo indebolirebbe l'argomento invece di rafforzarlo.

Un punto irrisolto: la FSF sostiene che le condizioni del Mac App Store siano
"restrizioni ulteriori" vietate dalla GPL, e Apple in passato ha tolto GNU Go e
VLC per questo. Krita però ci sta oggi con la GPLv3. Nessuna fonte primaria
scioglie la contraddizione. **Vendere dal proprio sito evita la domanda del
tutto**, ed è un motivo in più per farlo.

## In pratica, nell'ordine

1. iscriviti all'Apple Developer Program e prendi il certificato Developer ID
2. apri l'account Polar e passa la verifica (mettici in conto due settimane)
3. accendi GitHub Sponsors in parallelo
4. metti il link di Polar nella costante `PAGAMENTO` in `site/index.html`
5. `./tools/rilascia.sh <versione>` e carica il DMG firmato come release
6. il tap Homebrew, se lo vuoi, dopo: `brew tap eugenionerelli/plancia`
