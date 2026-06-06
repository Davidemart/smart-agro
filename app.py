from flask import Flask, request, jsonify
from services.vision_service import VisionService
from services.camera_service import CameraService
from repositories.db_repo import DBRepository
from utils.logger import logger

app = Flask(__name__)

# Inizializzazione dei servizi a livello di bootstrap (fase di avvio)
# Questo garantisce che i modelli vengano caricati in memoria una sola volta
try:
    logger.info("Inizializzazione dei servizi durante il bootstrap del server...")
    vision_service = VisionService()
    camera_service = CameraService()
    db_repository = DBRepository()
    logger.info("Servizi inizializzati con successo durante il bootstrap.")
except Exception as e:
    logger.critical(f"Errore critico durante il bootstrap dei servizi: {e}")
    # Definiamo delle istanze nulle in caso di crash critico, per evitare il blocco all'importazione
    # ma consentire al middleware degli errori di gestire la situazione a runtime.
    vision_service = None
    camera_service = None
    db_repository = None


def create_dialogflow_response(text_message):
    """Genera un dizionario conforme allo schema di risposta Dialogflow ES Fulfillment."""
    return {
        "fulfillmentMessages": [
            {
                "text": {
                    "text": [text_message]
                }
            }
        ]
    }


# =====================================================================
# HANDLERS DEGLI INTENTI (Pattern Strategy)
# =====================================================================

def handle_analisi_pianta(parameters):
    """
    Gestisce l'intento di analisi della pianta:
    Acquisisce il frame, esegue la pipeline di visione, salva i risultati e restituisce il testo.
    """
    if vision_service is None or camera_service is None or db_repository is None:
        raise RuntimeError("Servizi di backend non inizializzati correttamente a causa di un errore di boot.")

    logger.info("Avvio del flusso 'AnalisiPianta'...")
    
    # 1. Cattura del frame dalla webcam
    frame = camera_service.capture_frame()
    
    # 2. Elaborazione tramite pipeline di visione (con timeout interno a 3500ms)
    analysis = vision_service.analyse_frame(frame)
    
    species = analysis["species"]
    anomaly_pct = analysis["anomaly_pct"]
    seedling_count = analysis["seedling_count"]
    health_status = analysis["health_status"]
    
    # 3. Salvataggio su database MySQL (con gestione transazione/rollback)
    try:
        db_repository.save_observation(
            species_name=species,
            health_status=health_status,
            anomaly_pct=anomaly_pct,
            seedling_count=seedling_count
        )
        db_saved_msg = "I dati sono stati salvati correttamente nel database."
    except Exception as db_err:
        logger.error(f"Impossibile salvare l'osservazione nel DB: {db_err}")
        db_saved_msg = "Attenzione: non è stato possibile salvare i dati nel database, ma l'analisi è stata completata."

    # 4. Generazione della risposta dinamica
    # Logica di fallback se la specie non è identificata
    if species == "Specie Non Identificata":
        suggestion = "\nSuggerimento: Prova a posizionare meglio la foglia o a migliorare l'illuminazione dell'ambiente."
    else:
        suggestion = ""

    response_text = (
        f"Analisi Smart-Agri completata.\n"
        f"- Specie Vegetale: {species}\n"
        f"- Stato Sanitario: {health_status}\n"
        f"- Area con Anomalie: {anomaly_pct}%\n"
        f"- Piantine Rilevate: {seedling_count}\n\n"
        f"{db_saved_msg}{suggestion}"
    )
    
    return create_dialogflow_response(response_text)


def handle_saluto(parameters):
    """Gestisce un intento di saluto semplice."""
    return create_dialogflow_response(
        "Ciao! Sono l'assistente Smart-Agri. Posso avviare l'analisi delle tue piante in tempo reale. Dimmi pure quando procedere!"
    )


# Mappa degli intenti registrati (Pattern Strategy)
# La chiave corrisponde al queryResult['intent']['displayName'] impostato su Dialogflow
INTENT_ROUTING = {
    "AnalisiPianta": handle_analisi_pianta,
    "Saluto": handle_saluto
}

# =====================================================================
# ENDPOINT E ROUTING PRINCIPALE
# =====================================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint unico per il Dialogflow Fulfillment webhook."""
    req_data = request.get_json(silent=True)
    if not req_data or 'queryResult' not in req_data:
        logger.warning("Ricevuto payload non valido o vuoto.")
        return jsonify(create_dialogflow_response("Errore: Payload non conforme alle specifiche Dialogflow.")), 400

    # Estrazione dell'intento
    intent_name = req_data.get('queryResult', {}).get('intent', {}).get('displayName', '')
    parameters = req_data.get('queryResult', {}).get('parameters', {})
    
    logger.info(f"Ricevuta richiesta webhook. Intento: '{intent_name}'")

    # Pattern Strategy: Dispatching dell'intento
    handler = INTENT_ROUTING.get(intent_name)
    if handler:
        try:
            response_payload = handler(parameters)
            return jsonify(response_payload)
        except TimeoutError as te:
            logger.error(f"Errore di timeout durante l'esecuzione dell'handler: {te}")
            # Fallback in caso di superamento del tempo di elaborazione (3.5s)
            fallback_text = (
                "L'elaborazione delle immagini sta richiedendo più tempo del previsto. "
                "Si prega di verificare la connessione della telecamera o riprovare tra qualche istante."
            )
            return jsonify(create_dialogflow_response(fallback_text))
    else:
        logger.warning(f"Intento '{intent_name}' non gestito dal webhook.")
        default_text = f"Ricevuto intento '{intent_name}', ma non è configurata alcuna gestione nel webhook."
        return jsonify(create_dialogflow_response(default_text))


# =====================================================================
# MIDDLEWARE DI CATTURA ERRORI GLOBALE
# =====================================================================

@app.errorhandler(Exception)
def handle_global_exceptions(error):
    """
    Middleware globale per la gestione delle eccezioni.
    Intercetta qualsiasi crash imprevisto nel controller, nei servizi o nel repository.
    Registra l'errore nei log ed evita il crash HTTP restituendo a Dialogflow un JSON di cortesia valido.
    """
    logger.error(f"MIDDLEWARE ERROR: Eccezione non gestita catturata a livello globale: {error}", exc_info=True)
    
    friendly_message = (
        "Spiacente, si è verificato un errore tecnico interno durante l'analisi. "
        "I nostri sistemi di monitoraggio sono stati notificati. Riprova tra poco."
    )
    # Restituisce codice HTTP 200 con payload Dialogflow per non bloccare la chat dell'utente
    return jsonify(create_dialogflow_response(friendly_message)), 200
