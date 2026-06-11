# Smart-Agri: Sistema di Analisi Piante con Dialogflow e Computer Vision

Questo progetto è un'applicazione backend sviluppata in **Flask** che funge da *Fulfillment Webhook* per un assistente conversazionale creato con **Dialogflow**. 
Il sistema integra tecniche di **Computer Vision** e **Machine Learning** per analizzare in tempo reale lo stato di salute delle piante, riconoscerne la specie e contare le piantine presenti, salvando le osservazioni in un database MySQL.

## 🧠 Come Funziona il Progetto

L'architettura si basa sull'interazione di tre componenti principali:

1. **Dialogflow (L'Interfaccia):** L'utente parla con l'assistente (es. *"Analizza la mia pianta"*). Dialogflow riconosce l'intento e invia una richiesta al server Flask.
2. **Computer Vision (L'Occhio):** Il server acquisisce un'immagine dalla webcam tramite **OpenCV**. In caso di indisponibilità della telecamera, è previsto un sistema di fallback su immagini di test (cartella `test_images`).
3. **Machine Learning e Analisi (Il Cervello):** L'immagine viene passata a una pipeline di elaborazione:
    * **Fase A - Classificazione Specie:** Un modello **Keras/TensorFlow** (addestrato esternamente) riconosce la specie della pianta (es. Pomodoro o Basilico).
    * **Fase B - Analisi Clorotica:** OpenCV analizza l'immagine nello spazio colore HSV isolando le macchie gialle, calcolando così la percentuale di area "malata".
    * **Fase C - Conteggio:** La libreria **cvlib** (basata su YOLO) scansiona l'immagine per individuare e contare il numero di piantine.
4. **Database (La Memoria):** Tutti i dati raccolti vengono salvati su un DB **MySQL**.
5. **Risposta:** Flask confeziona un riepilogo testuale formattato appositamente per Dialogflow e lo restituisce per essere letto all'utente.

---

## 🚀 Guida all'Installazione

### Prerequisiti
* Python 3.8+
* Database MySQL installato e in esecuzione
* Una webcam funzionante
* [Ngrok](https://ngrok.com/) (per esporre il server locale su Internet)

### 1. Clonazione del Progetto
Scarica il progetto e posizionati nella cartella root:
```bash
git clone <URL_DEL_REPO>
cd smart-agro
```

### 2. Installazione delle Dipendenze
Si raccomanda l'uso di un ambiente virtuale (Virtual Environment). Installa le librerie necessarie con:
```bash
pip install -r requirements.txt
```

### 3. Configurazione dell'Ambiente (.env)
Crea il file di configurazione clonando quello di esempio:
```bash
cp .env.example .env
```
Apri il file `.env` appena creato e compila i tuoi parametri, in particolare i dati di accesso MySQL:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=la_tua_password
DB_NAME=smart_agri

# Usa 0 per la webcam predefinita o "test" per usare le immagini di fallback.
CAMERA_INDEX=0 
```

### 4. Setup Database e Modelli
1. Importa la struttura delle tabelle nel tuo database MySQL utilizzando il file `schema.sql`.
2. Verifica che i file del modello neurale (`keras_model.h5` e `labels.txt`) siano fisicamente presenti dentro la cartella `models/`.

---

## 🏃 Come Eseguire il Progetto

Per mettere in funzione l'applicazione e connetterla a Dialogflow, esegui questi tre passaggi in sequenza:

### Step 1: Avvia il Server Flask
Nel terminale, lancia l'applicazione con:
```bash
python run.py
```
*Il server si avvierà in locale, in ascolto sulla porta indicata in configurazione (di default `5000`).*

### Step 2: Avvia Ngrok
Apri un secondo terminale e utilizza Ngrok per creare un tunnel sicuro verso la tua porta 5000:
```bash
ngrok http 5000
```
Copia l'URL pubblico HTTPS che viene generato (es. `https://1234abcd.ngrok-free.app`).

### Step 3: Configura il Webhook su Dialogflow
1. Vai sulla Console di Dialogflow.
2. Clicca sulla voce **Fulfillment** nel menu laterale.
3. Attiva lo switch relativo al **Webhook**.
4. Incolla l'URL HTTPS di Ngrok, ricordandoti di aggiungere `/webhook` alla fine (es. `https://1234abcd.ngrok-free.app/webhook`).
5. Salva in fondo alla pagina.
6. Apri la sezione **Intents**, seleziona `AnalisiPianta` e, in fondo, assicurati che sia attivata l'opzione *"Enable webhook call for this intent"*.

🎉 **Sistema Pronto!** Adesso puoi simulare una conversazione o testare l'integrazione chiedendo all'assistente di effettuare un'analisi.

---

## 📂 Struttura delle Cartelle
* `app.py`: File centrale che espone l'endpoint `/webhook` e instrada gli intenti.
* `run.py`: Lo script di avvio principale del server.
* `config.py`: Lettura e validazione delle variabili in `.env`.
* `services/`: La "Business Logic". Qui trovi l'interazione con la camera (`camera_service.py`) e l'analisi visiva (`vision_service.py`).
* `repositories/`: Connessione diretta con MySQL (`db_repo.py`).
* `models/`: Deve contenere i file del modello AI Keras/Tensorflow.
* `utils/`: Funzioni trasversali di utilità (es. logging).
