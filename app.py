from flask import Flask, request, jsonify
from services.vision_service import VisionService
from services.camera_service import CameraService
from repositories.db_repo import DBRepository
from utils.logger import logger

app = Flask(__name__)

# Inizializzazione dei servizi a livello di bootstrap (fase di avvio)
# Questo garantisce che i modelli vengano caricati in memoria una sola volta
vision_service = None
camera_service = None
db_repository = None

logger.info("Inizializzazione dei servizi durante il bootstrap del server...")
try:
    vision_service = VisionService()
except Exception as e:
    logger.critical(f"Errore inizializzazione VisionService: {e}")

try:
    camera_service = CameraService()
except Exception as e:
    logger.critical(f"Errore inizializzazione CameraService: {e}")

try:
    db_repository = DBRepository()
except Exception as e:
    logger.critical(f"Errore inizializzazione DBRepository (Il DB non è accessibile, verranno simulati i salvataggi): {e}")

logger.info("Fase di bootstrap servizi completata.")


def create_dialogflow_response(text_message, output_contexts=None):
    """Genera un dizionario conforme allo schema di risposta Dialogflow ES Fulfillment."""
    response = {
        "fulfillmentMessages": [
            {
                "text": {
                    "text": [text_message]
                }
            }
        ]
    }
    # Se passiamo dei contesti (memoria), li aggiunge al JSON
    if output_contexts:
        response["outputContexts"] = output_contexts
    return response


# =====================================================================
# HANDLERS DEGLI INTENTI (Pattern Strategy)
# =====================================================================

def handle_analizza_serra(parameters):
    """
    Gestisce l'intento AnalizzaSerra (Panoramica generale).
    Restituisce solo il conteggio, la specie e la posizione di ogni pianta.
    """
    if vision_service is None or camera_service is None:
        raise RuntimeError("Servizi non inizializzati.")

    logger.info("Avvio flusso panoramica: AnalizzaSerra")
    
    frame = camera_service.capture_frame()
    analysis = vision_service.analyse_frame(frame)
    
    seedling_count = analysis["seedling_count"]
    plants = analysis["plants"]
    
    if seedling_count == 0 or not plants:
        return create_dialogflow_response(
            "Ho controllato la serra, ma non ho rilevato alcuna piantina. Prova a sistemare l'inquadratura."
        )

    # Crea un elenco puntato con posizione e specie
    plant_reports = []
    for plant in plants:
        plant_reports.append(f"• Posizione {plant['plant_id']}: {plant['species']}")
    
    plants_summary = "\n".join(plant_reports)
    risposta = (
        f"Analisi generale della serra completata.\n"
        f"Ho rilevato {seedling_count} piantine nell'inquadratura:\n{plants_summary}\n\n"
        f"Se vuoi sapere come sta una di queste, chiedimi ad esempio: 'Come sta la pianta 1?'"
    )

    if seedling_count > 0 and db_repository is not None:
        try:
            db_repository.save_plants_from_serra(plants)
        except Exception as e:
            logger.error(f"Errore durante la mappatura delle piante: {e}")

    return create_dialogflow_response(risposta)


def handle_analizza_pianta(parameters):
    """
    Gestisce l'intento AnalizzaPianta (Dettaglio singola pianta).
    Analizza lo stato di salute e le anomalie di una specifica posizione.
    Imposta il contesto per le domande successive (consigli, wiki).
    """
    if vision_service is None or camera_service is None:
        raise RuntimeError("Servizi non inizializzati.")

    # Estrazione del parametro obbligatorio 'number' definito su Dialogflow
    num_richiesto = parameters.get("number", "")
    if isinstance(num_richiesto, list) and num_richiesto:
        num_richiesto = int(num_richiesto[0])
    elif num_richiesto:
        num_richiesto = int(num_richiesto)
    else:
        return create_dialogflow_response("Di quale pianta vuoi sapere lo stato? Dimmi il suo numero.")

    logger.info(f"Avvio flusso dettaglio: AnalizzaPianta per la posizione {num_richiesto}")

    frame = camera_service.capture_frame()
    analysis = vision_service.analyse_frame(frame)
    
    plants = analysis["plants"]
    seedling_count = analysis["seedling_count"]

    # Cerca la pianta con l'ID richiesto
    pianta_trovata = next((p for p in plants if p["plant_id"] == num_richiesto), None)
    
    if pianta_trovata:
        # ... (il codice di salvataggio DB rimane uguale) ...

        risposta = (
            f"Ecco il report per la Pianta {num_richiesto} ({pianta_trovata['species']}).\n"
            f"• Stato di salute: {pianta_trovata['health_status']}\n"
            f"• Area con anomalie cromatiche: {pianta_trovata['anomaly_pct']}%\n\n"
            f"Posso darti dei consigli su come curarla o spiegarti di più su questa coltura. Cosa preferisci?"
        )
        
        # --- NOVITÀ: Salviamo la specie nella memoria di Dialogflow ---
        req_data = request.get_json(silent=True)
        session = req_data.get("session")  # ID univoco della chat utente
        
        contesti_uscita = [{
            "name": f"{session}/contexts/analizzapianta-followup",
            "lifespanCount": 5, # Ricorderà la specie per i prossimi 5 messaggi
            "parameters": {
                "specie_vegetale": pianta_trovata['species'].lower()
            }
        }]
        
        return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
    else:
        risposta = f"Hai chiesto della pianta {num_richiesto}, ma attualmente nell'inquadratura ne vedo solo {seedling_count}."
        return create_dialogflow_response(risposta)


def handle_saluto(parameters):
    """Gestisce un intento di saluto semplice."""
    return create_dialogflow_response(
        "Ciao! Sono l'assistente Smart-Agri. Posso avviare l'analisi delle tue piante in tempo reale. Dimmi pure quando procedere!"
    )

#TODO: Nella wiki metteremo: nome scientifico, zona, info generali, ecc...
def handle_consigli_specie(parameters):
    """
    Gestisce l'intento ConsigliSpecie.
    """
    logger.info(f"Ricevuti parametri diretti: {parameters}")
    
    # 1. Recuperiamo l'intero payload inviato da Dialogflow
    req_data = request.get_json(silent=True)
    
    specie_raw = parameters.get("specie_vegetale", "")
    supporto_raw = parameters.get("supporto", "")
    
    # --- NOVITÀ: Se la specie è vuota o generica, la peschiamo dal contesto ---
    if not specie_raw or specie_raw == "pianta":
        contesti = req_data.get("queryResult", {}).get("outputContexts", [])
        for ctx in contesti:
            if "analizzapianta-followup" in ctx.get("name", "").lower():
                parametri_contesto = ctx.get("parameters", {})
                if "specie_vegetale" in parametri_contesto:
                    specie_raw = parametri_contesto["specie_vegetale"]
                    logger.info(f"Specie recuperata dalla memoria (contesto): {specie_raw}")
                    break

    # 2. Normalizzazione
    specie = specie_raw[0].lower() if isinstance(specie_raw, list) and specie_raw else str(specie_raw).lower()
    supporto = supporto_raw[0].lower() if isinstance(supporto_raw, list) and supporto_raw else str(supporto_raw).lower()
    
    # Se la specie continua a non esserci, usiamo un default
    if not specie:
        specie = "pianta"
        
    # Mini Knowledge-Base (Wiki)
    wiki = {
        "pomodoro": {
            "acqua": "Il pomodoro necessita di annaffiature abbondanti e regolari, ma evita i ristagni e non bagnare le foglie.",
            "sole": "I pomodori amano l'esposizione in pieno sole per maturare correttamente.",
            "concime": "Usa un concime ricco di potassio e fosforo durante la fioritura.",
            "preservare": "Per evitare l'ingiallimento e i funghi, garantisci un buon circolo d'aria e usa trattamenti rameici se necessario."
        },
        "basilico": {
            "acqua": "Il basilico vuole un terreno sempre umido, ma ben drenato. Annaffialo la mattina presto.",
            "sole": "Preferisce zone luminose ma al riparo dal sole diretto cocente delle ore centrali.",
            "concime": "Un fertilizzante azotato ogni 15 giorni aiuta a mantenere le foglie rigogliose.",
            "preservare": "Per preservare la pianta, cima i fiori appena spuntano: così le foglie manterranno il loro aroma."
        },
        "alloro": {
            "acqua": "L'alloro resiste bene alla siccità. Annaffia solo quando il terreno è completamente asciutto.",
            "sole": "Cresce bene sia al sole che a mezz'ombra.",
            "concime": "Non richiede concimazioni frequenti; basta un po' di stallatico in primavera.",
            "preservare": "Proteggilo dalle cocciniglie controllando periodicamente la pagina inferiore delle foglie."
        }
    }

    # Risposta dinamica basata sui parametri
    if specie in wiki and supporto in wiki[specie]:
        risposta = f"🌿 Consigli per {specie.capitalize()} (Tema: {supporto}):\n{wiki[specie][supporto]}"
    elif supporto:
        risposta = f"In generale, per quanto riguarda '{supporto}', assicurati sempre di non esagerare per non stressare la pianta. Hai bisogno di dettagli su una specie in particolare come pomodoro o basilico?"
    else:
        risposta = "Non ho capito esattamente quale consiglio ti serve. Prova a chiedermi dell'acqua, del sole o del concime per una specifica pianta."

    return create_dialogflow_response(risposta)

# Mappa degli intenti registrati (Pattern Strategy)
# La chiave corrisponde al queryResult['intent']['displayName'] impostato su Dialogflow
INTENT_ROUTING = {
    "Default Welcome Intent": handle_saluto,
    "AnalizzaSerra": handle_analizza_serra,
    "AnalizzaPianta": handle_analizza_pianta,
    "ConsigliSpecie": handle_consigli_specie
    #"WikiSpecie": handle_wiki_specie,         
    #"ProblemiPianta": handle_problemi_pianta  
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
