# Implementation Plan: Nuove funzionalità per Smart-Agri

Questa è la proposta per l'implementazione delle tre funzionalità richieste nel progetto `smart-agro`.

## User Review Required
> [!IMPORTANT]
> **Approvazione Modello Dati**: Per la Wiki, mi baserò sulla tabella `wiki_species` presente in `schema.sql`
> **Chat Vocale**: La chat vocale sarà implementata usando le API Web native del browser (Web Speech API) per convertire la voce in testo e inviarla a Dialogflow. Questo richiederà i permessi del microfono nel browser.

## Open Questions
- **Dati Wiki**: Vuoi che io inserisca dei dati predefiniti (es. per pomodoro, basilico, alloro, rosmarino) nella nuova tabella del database per la Wiki? Si
- **Soglia Anomalie**: Attualmente il codice per la maschera HSV prevede una soglia del 30% di giallo per definire lo stato "Critico". Ti va bene questa soglia o preferisci un valore diverso? Usa il 40%

## Proposed Changes

---
### 1. Intenti Wiki (Database, Dialogflow, Backend)

#### [MODIFY] [db_repo.py](file:///c:/Users/nicol/Desktop/Progetto_Academy/smart-agro/repositories/db_repo.py) (da modificare)
- Aggiunta del metodo `get_wiki_info(species)` per interrogare la nuova tabella `wiki_species`.
- Aggiunta della logica per popolare la tabella se vuota all'avvio.

#### [NEW] [WikiSpecie.json](file:///c:/Users/nicol/Desktop/Progetto_Academy/smart-agro/Dialog_Flow/WikiSpecie.json)
- Creazione del nuovo intento Dialogflow per gestire le domande sulla Wiki (es. "Qual è il nome scientifico del pomodoro?", "Dimmi del clima per il basilico"); vincolate rigidamente alle 4 macro-aree: Nomenclatura, Descrizione, Coltivazione e Usi.
- Il parametro `specie_vegetale` sarà obbligatorio, con possibilità di recuperarlo dal contesto.
- Configurazione dell'intento (tramite entità di supporto come sezione_richiesta) per permettere al backend di identificare quale dei 4 macro-temi l'utente sta toccando con la sua domanda (es. se chiede "qual è il nome scientifico", Dialogflow deve aiutare il backend a capire che parliamo dell'area Nomenclatura).

#### [MODIFY] [app.py](file:///c:/Users/nicol/Desktop/Progetto_Academy/smart-agro/app.py)
- Aggiunta della funzione `handle_wiki_specie(parameters)` per elaborare l'intento `WikiSpecie`.
- Estrazione della specie richiesta, query al DB tramite `db_repository.get_wiki_info()`, e formattazione della risposta.
- Registrazione dell'handler nel dizionario `INTENT_ROUTING`.
- Implementazione della logica di risposta a blocchi: la funzione identificherà la macro-area della domanda e, indipendentemente dalla specificità del quesito, formatterà la risposta includendo tutti i rispettivi campi di appartenenza:
  - **Nomenclatura** (es. "Cos'è l'alloro?", "Qual è la famiglia del pomodoro?"): restituisce *Nome Scientifico*, *Nome Comune* e *Famiglia Botanica*.
  - **Descrizione** (es. "Quanto cresce?", "Di dove è originario?"): restituisce *Portamento*, *Altezza Massima* e *Regione d'Origine*.
  - **Coltivazione** (es. "Quanta acqua vuole?", "Resiste al freddo?"): restituisce *Esposizione*, *Bisogno Idrico*, *Tipo di Terreno* e *Temperatura Minima*.
  - **Usi** (es. "È velenoso?", "A cosa serve?"): restituisce *Tossicità (is_toxic)* e *Usi Principali*.

---
### 2. Valutazione Stato Sanitario (OpenCV HSV)

#### [MODIFY] [vision_service.py](file:///c:/Users/nicol/Desktop/Progetto_Academy/smart-agro/services/vision_service.py)
- Decommento e ripristino del blocco di codice relativo alla Segmentazione delle Anomalie tramite OpenCV.
- Calcolo della percentuale di pixel gialli (`anomaly_pct`) all'interno della bounding box della singola pianta.
- Aggiornamento di `health_status` a "Critico - Rilevato forte ingiallimento" se l'area gialla supera il 40%.
- Modifica del colore del riquadro nell'immagine (`annotated_frame`): da sempre verde a verde/rosso in base al superamento della soglia di anomalia.

---
### 3. Implementazione Chat Vocale

#### [MODIFY] [index.html](file:///c:/Users/nicol/Desktop/Progetto_Academy/smart-agro/static/index.html)
- Aggiunta di un pulsante "Microfono" (Floating Action Button o integrato nella sidebar) per attivare l'ascolto vocale.
- Integrazione della **Web Speech API (`SpeechRecognition`)** in JavaScript per catturare l'audio dell'utente e convertirlo in testo.
- Iniezione del testo riconosciuto all'interno dell'interfaccia di `<df-messenger>` e invio automatico al bot.
- Aggiunta del supporto alla sintesi vocale (**Web Speech API `SpeechSynthesis`**) per leggere ad alta voce la risposta di testo restituita da Dialogflow (opzionale/attivabile tramite toggle).

## Verification Plan

### Manual Verification
1. **Wiki**: Inviare una richiesta via chat ("qual è il nome scientifico del pomodoro?") e verificare che la risposta provenga correttamente dal database.
2. **OpenCV**: Mostrare alla telecamera un'immagine o un oggetto con molto giallo. Verificare che l'immagine annotata presenti un riquadro rosso e che la percentuale di anomalia sia > 30%.
3. **Chat Vocale**: Aprire la Web App, cliccare sul nuovo pulsante microfono, pronunciare una frase ("Analizza la serra") e verificare che venga inviata a Dialogflow e processata correttamente.
