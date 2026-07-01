import os
from flask import Flask, request, jsonify, send_from_directory, send_file
from services.vision_service import VisionService
from services.camera_service import CameraService
from services.analysis_cache import analysis_cache
from services.background_worker import BackgroundWorker
from repositories.db_repo import DBRepository
from utils.logger import logger

app = Flask(__name__)

# Inizializzazione dei servizi a livello di bootstrap (fase di avvio)
# Questo garantisce che i modelli vengano caricati in memoria una sola volta
vision_service = None
camera_service = None
db_repository = None
background_worker = None
session_memory = {}  # Memoria locale per tracciare specie e posizione per ciascuna sessione


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

# Avvio del worker di background (aggiorna la cache ogni 10 secondi)
# Gli handler Dialogflow leggeranno sempre dalla cache → risposta istantanea
if camera_service is not None and vision_service is not None:
    try:
        background_worker = BackgroundWorker(
            camera_service=camera_service,
            vision_service=vision_service,
            cache=analysis_cache,
            interval_seconds=900
        )
        background_worker.start()
        logger.info("Worker di background avviato con successo.")
    except Exception as e:
        logger.critical(f"Errore avvio BackgroundWorker: {e}")
else:
    logger.warning("BackgroundWorker NON avviato: camera_service o vision_service non disponibili.")

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
    Legge il risultato dalla cache aggiornata dal worker in background,
    garantendo una risposta entro il timeout di 5s di Dialogflow.
    """
    # --- Lettura dalla cache (invece di catturare il frame in tempo reale) ---
    if not analysis_cache.is_ready:
        return create_dialogflow_response(
            "Sto ancora completando la prima analisi della serra. Riprova tra qualche secondo!"
        )
    analysis = analysis_cache.result
    logger.info(f"[AnalizzaSerra] Letto dalla cache (età: {analysis_cache.age_seconds:.1f}s).")

    # 1. Estrazione e normalizzazione del parametro specie_vegetale
    specie_richiesta = parameters.get("specie_vegetale", "")
    if isinstance(specie_richiesta, list) and specie_richiesta:
        specie_richiesta = str(specie_richiesta[0]).lower()
    else:
        specie_richiesta = str(specie_richiesta).lower()

    # Verifica se l'utente si riferisce a una specie specifica (escludendo termini generici)
    is_specie_specifica = specie_richiesta and specie_richiesta not in ["pianta", "piante", "piantina", "piantine", "colture", "coltura"]

    # Lettura del payload completo (usato sia per query_text che per session)
    req_data = request.get_json(silent=True)
    query_text = ""
    session = None
    if req_data:
        query_text = req_data.get("queryResult", {}).get("queryText", "").lower()
        session = req_data.get("session")

    # Parole chiave generiche e specifiche nel testo dell'utente
    generic_keywords = ["piant", "piantin", "coltur", "serra", "generale", "tutt"]
    specific_keywords = ["pomodor", "basilic", "allor", "rosmarin"]

    contiene_generico = any(x in query_text for x in generic_keywords)
    contiene_specifico = any(x in query_text for x in specific_keywords)

    # Se il testo digitato contiene parole generiche e non contiene specie specifiche,
    # forziamo il comportamento generico (sovrascrivendo l'eredità automatica di Dialogflow)
    if contiene_generico and not contiene_specifico:
        is_specie_specifica = False

    logger.info(f"Avvio flusso panoramica: AnalizzaSerra (Specie richiesta: {specie_richiesta}, Specifica: {is_specie_specifica})")
    
    seedling_count = analysis["seedling_count"]
    plants = analysis["plants"]
    
    if seedling_count == 0 or not plants:
        if session:
            session_memory[session] = {
                "specie_vegetale": "",
                "number": ""
            }
            logger.info(f"[Memory Reset - Serra Vuota] Reimpostata session_memory per sessione: {session}")
        return create_dialogflow_response(
            "Ho controllato la serra, ma non ho rilevato alcuna piantina. Prova a sistemare l'inquadratura."
        )

    # Salvataggio delle piante nel database
    if db_repository is not None:
        try:
            db_repository.save_plants_from_serra(plants)
        except Exception as e:
            logger.error(f"Errore durante la mappatura delle piante: {e}")

    # Gestione query per specie specifica
    if is_specie_specifica:
        piante_specie = [p for p in plants if p["species"].lower() == specie_richiesta]
        
        # Estrazione dell'azione (es. "quanti" vs "come sta")
        azione_raw = parameters.get("azione", "")
        azione = azione_raw[0].lower() if isinstance(azione_raw, list) and azione_raw else str(azione_raw).lower()
        chiede_conteggio = "quant" in azione or ("conta" in azione and "controll" not in azione) or "qual è il numero" in azione

        if chiede_conteggio:
            count_specie = len(piante_specie)
            if count_specie == 0:
                risposta = f"Ho controllato la serra, ma attualmente non vedo alcuna pianta di '{specie_richiesta.capitalize()}' nell'inquadratura."
            elif count_specie == 1:
                risposta = f"Nell'inquadratura è presente 1 pianta di '{specie_richiesta.capitalize()}'."
            else:
                risposta = f"Nell'inquadratura sono presenti {count_specie} piante di '{specie_richiesta.capitalize()}'."
            
            # Reset del contesto se non trovata, altrimenti salva/aggiorna
            contesti_uscita = None
            if session:
                session_memory[session] = {
                    "specie_vegetale": specie_richiesta if count_specie > 0 else "",
                    "number": piante_specie[0]["plant_id"] if count_specie > 0 else ""
                }
                logger.info(f"[Memory Save - Serra Count] Salvato in sessione: {session_memory[session]}")
                lifespan = 5 if count_specie > 0 else 0
                contesti_uscita = [{
                    "name": f"{session}/contexts/analizzapianta-followup",
                    "lifespanCount": lifespan,
                    "parameters": {
                        "specie_vegetale": specie_richiesta if count_specie > 0 else ""
                    }
                }]
            return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
        else:
            # Chiede lo stato di salute
            if not piante_specie:
                risposta = f"Ho scansionato la serra, ma attualmente non vedo alcuna pianta di '{specie_richiesta.capitalize()}' nell'inquadratura."
                
                # Reset del contesto
                contesti_uscita = None
                if session:
                    contesti_uscita = [{
                        "name": f"{session}/contexts/analizzapianta-followup",
                        "lifespanCount": 0,
                        "parameters": {}
                    }]
                return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
            
            reports = []
            for p in piante_specie:
                # Salva l'osservazione nel database per ciascuna pianta
                if db_repository is not None:
                    try:
                        db_repository.save_single_observation(
                            position=p["plant_id"],
                            health_status=p["health_status"],
                            anomaly_pct=p["anomaly_pct"],
                            seedling_count=seedling_count
                        )
                    except Exception as e:
                        logger.error(f"Errore salvataggio osservazione nel database per pianta {p['plant_id']}: {e}")

                reports.append(
                    f"• {specie_richiesta.capitalize()} (Posizione {p['plant_id']}): "
                    f"Stato {p['health_status']}, Anomalie cromatiche {p['anomaly_pct']}%."
                )
            
            risposta = (
                f"Ecco lo stato di salute per le piante di {specie_richiesta.capitalize()} rilevate:\n"
                + "\n".join(reports) + "\n\n"
                + f"Vuoi che ti dia qualche consiglio sulla cura del {specie_richiesta}?"
            )

            # Aggiunta contesto di followup per eventuali richieste di consigli successive
            contesti_uscita = None
            if session:
                session_memory[session] = {
                    "specie_vegetale": specie_richiesta,
                    "number": piante_specie[0]["plant_id"]  # Primo ID come riferimento di default
                }
                logger.info(f"[Memory Save - Serra Health] Salvato in sessione: {session_memory[session]}")
                contesti_uscita = [{
                    "name": f"{session}/contexts/analizzapianta-followup",
                    "lifespanCount": 5,
                    "parameters": {
                        "specie_vegetale": specie_richiesta
                    }
                }]
            return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

    # Se la richiesta è generica, seguiamo il comportamento standard
    plant_reports = []
    for plant in plants:
        plant_reports.append(f"• Posizione {plant['plant_id']}: {plant['species']}")
    
    plants_summary = "\n".join(plant_reports)
    risposta = (
        f"Analisi generale della serra completata.\n"
        f"Ho rilevato {seedling_count} piantine nell'inquadratura:\n{plants_summary}\n\n"
        f"Se vuoi sapere come sta una di queste, chiedimi ad esempio: 'Come sta la pianta 1?'"
    )

    # Reset del contesto (richiesta generica: nessuna specie specifica in memoria)
    contesti_uscita = None
    if session:
        session_memory[session] = {
            "specie_vegetale": "",
            "number": ""
        }
        logger.info(f"[Memory Reset - Serra Generica] Reimpostata session_memory per sessione: {session}")
        contesti_uscita = [{
            "name": f"{session}/contexts/analizzapianta-followup",
            "lifespanCount": 0,
            "parameters": {}
        }]
    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)


def handle_analizza_pianta(parameters):
    """
    Gestisce l'intento AnalizzaPianta (Dettaglio singola pianta).
    Analizza lo stato di salute cercando sia per NUMERO (posizione) che per NOME SPECIE.
    Legge il risultato dalla cache aggiornata dal worker in background.
    """
    # 0. Lettura payload (una sola volta per tutta la funzione)
    req_data = request.get_json(silent=True)
    session = req_data.get("session") if req_data else None

    # 1. Estrazione dei due possibili parametri da Dialogflow
    num_richiesto = parameters.get("number", "")
    specie_richiesta = parameters.get("specie_vegetale", "")

    # Normalizzazione specie richiesta
    # Gestisce tutte le forme restituite da Dialogflow: lista non vuota, lista vuota, stringa
    if isinstance(specie_richiesta, list):
        specie_richiesta = str(specie_richiesta[0]).lower() if specie_richiesta else ""
    else:
        specie_richiesta = str(specie_richiesta).lower()

    # Pulisce prefissi generici come "pianta di ", "piantina di ", etc.
    for prefix in ["pianta di ", "piantina di ", "piantine di ", "coltura di ", "colture di ", "pianta ", "piantina ", "piantine ", "coltura ", "colture "]:
        if specie_richiesta.startswith(prefix):
            specie_richiesta = specie_richiesta[len(prefix):].strip()

    # Se non c'è né il numero né la specie, o se è rimasta solo una parola generica
    if not num_richiesto and (not specie_richiesta or specie_richiesta in ["pianta", "piantina", "piantine", "coltura", "colture"]):
        return create_dialogflow_response("Di quale pianta vuoi sapere lo stato? Dimmi il suo numero o la sua specie.")

    # --- Lettura dalla cache (invece di catturare il frame in tempo reale) ---
    if not analysis_cache.is_ready:
        return create_dialogflow_response(
            "Sto ancora completando la prima analisi della serra. Riprova tra qualche secondo!"
        )
    analysis = analysis_cache.result
    logger.info(f"[AnalizzaPianta] Letto dalla cache (età: {analysis_cache.age_seconds:.1f}s).")

    logger.info(f"Avvio flusso dettaglio: AnalizzaPianta (Numero richiesto: {num_richiesto}, Specie richiesta: {specie_richiesta})")
    
    plants = analysis["plants"]
    seedling_count = analysis["seedling_count"]

    # 3. Logica di ricerca flessibile (Risolve il bug!)
    pianta_trovata = None
    
    if num_richiesto is not None and num_richiesto != "" and num_richiesto != []:
        # Se l'utente ha detto il numero (es. "Pianta 1"), cerchiamo per ID posizione
        num_richiesto = int(num_richiesto[0]) if isinstance(num_richiesto, list) else int(num_richiesto)
        pianta_posizione = next((p for p in plants if p["plant_id"] == num_richiesto), None)
        
        if pianta_posizione:
            if specie_richiesta and specie_richiesta not in ["pianta", "piantina", "piantine", "coltura", "colture"]:
                # Verifichiamo la corrispondenza tra la specie richiesta e quella reale nella posizione
                if pianta_posizione["species"].lower() != specie_richiesta:
                    # Mismatch! Cerchiamo dove si trova la specie richiesta
                    piante_specie_corrette = [p for p in plants if p["species"].lower() == specie_richiesta]
                    if not piante_specie_corrette:
                        risposta = (
                            f"Nella posizione {num_richiesto} non c'è il {specie_richiesta.lower()}, "
                            f"ma c'è una pianta di {pianta_posizione['species'].lower()}. "
                            f"Non ho rilevato alcuna pianta di {specie_richiesta.lower()} nell'inquadratura."
                        )
                    else:
                        if len(piante_specie_corrette) == 1:
                            pos_info = f"in posizione {piante_specie_corrette[0]['plant_id']}"
                        else:
                            positions = [p["plant_id"] for p in piante_specie_corrette]
                            if len(positions) == 2:
                                pos_info = f"in posizione {positions[0]} o {positions[1]}"
                            else:
                                pos_info = "in posizione " + ", ".join(map(str, positions[:-1])) + f" o {positions[-1]}"
                        
                        risposta = (
                            f"Nella posizione {num_richiesto} non c'è il {specie_richiesta.lower()}, "
                            f"ma c'è una pianta di {pianta_posizione['species'].lower()}, "
                            f"il {specie_richiesta.lower()} è {pos_info}."
                        )
                    
                    # Impostiamo o resettiamo il contesto per eventuale follow-up
                    contesti_uscita = None
                    if session:
                        lifespan = 5 if piante_specie_corrette else 0
                        contesti_uscita = [{
                            "name": f"{session}/contexts/analizzapianta-followup",
                            "lifespanCount": lifespan,
                            "parameters": {
                                "specie_vegetale": specie_richiesta if piante_specie_corrette else ""
                            }
                        }]
                    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
            
            # Se la specie coincide o non è indicata
            pianta_trovata = pianta_posizione
    elif specie_richiesta:
        # Se l'utente ha detto il nome (es. "Pomodoro"), cerchiamo tutte le piante di quella specie
        piante_specie = [p for p in plants if p["species"].lower() == specie_richiesta]
        if len(piante_specie) == 1:
            pianta_trovata = piante_specie[0]
            num_richiesto = pianta_trovata["plant_id"] # Recuperiamo il numero di posizione reale per il DB
        elif len(piante_specie) > 1:
            # Caso duplicati! Rispondiamo con la concatenazione dei report per ciascuna pianta
            reports = []
            for p in piante_specie:
                # Salva l'osservazione nel database per ciascuna pianta
                if db_repository is not None:
                    try:
                        db_repository.save_single_observation(
                            position=p["plant_id"],
                            health_status=p["health_status"],
                            anomaly_pct=p["anomaly_pct"],
                            seedling_count=seedling_count
                        )
                    except Exception as e:
                        logger.error(f"Errore salvataggio osservazione nel database per pianta {p['plant_id']}: {e}")

                reports.append(
                    f"• {specie_richiesta.capitalize()} (Posizione {p['plant_id']}): "
                    f"Stato {p['health_status']}, Anomalie cromatiche {p['anomaly_pct']}%."
                )
            
            risposta = (
                f"Ecco lo stato di salute per le piante di {specie_richiesta.capitalize()} rilevate:\n"
                + "\n".join(reports) + "\n\n"
                + f"Vuoi che ti dia qualche consiglio sulla cura del {specie_richiesta}?"
            )
            
            contesti_uscita = None
            if session:
                session_memory[session] = {
                    "specie_vegetale": specie_richiesta.lower(),
                    "number": piante_specie[0]["plant_id"]
                }
                logger.info(f"[Memory Save] Salvato in sessione: {session_memory[session]}")
                contesti_uscita = [{
                    "name": f"{session}/contexts/analizzapianta-followup",
                    "lifespanCount": 5,
                    "parameters": {
                        "specie_vegetale": specie_richiesta,
                        "number": piante_specie[0]["plant_id"]  # Salva la prima pianta come riferimento
                    }
                }]

            return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

    # 4. Gestione del risultato e salvataggio
    if pianta_trovata:
        try:
            db_repository.save_single_observation(
                position=num_richiesto,
                health_status=pianta_trovata["health_status"],
                anomaly_pct=pianta_trovata["anomaly_pct"],
                seedling_count=seedling_count
            )
        except Exception as e:
            logger.error(f"Errore DB per pianta {num_richiesto}: {e}")

        # Componiamo una risposta personalizzata che mostra sia la posizione che la specie
        risposta = (
            f"Ecco il report in tempo reale per la pianta di {pianta_trovata['species'].capitalize()} (Posizione {num_richiesto}).\n"
            f"• Stato di salute: {pianta_trovata['health_status']}\n"
            f"• Area con anomalie cromatiche: {pianta_trovata['anomaly_pct']}%\n\n"
            f"Vuoi che ti dia qualche consiglio sulla cura del {pianta_trovata['species']}?"
        )
        
        # Salviamo la specie e il numero nel contesto di Dialogflow per le domande successive
        contesti_uscita = None
        if session:
            session_memory[session] = {
                "specie_vegetale": pianta_trovata['species'].lower(),
                "number": num_richiesto
            }
            logger.info(f"[Memory Save] Salvato in sessione: {session_memory[session]}")
            contesti_uscita = [{
                "name": f"{session}/contexts/analizzapianta-followup",
                "lifespanCount": 5,
                "parameters": {
                    "specie_vegetale": pianta_trovata['species'].lower(),
                    "number": num_richiesto
                }
            }]

        return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
    else:
        # Messaggio di cortesia se la pianta richiesta non è presente nell'inquadratura
        if num_richiesto is not None and num_richiesto != "" and num_richiesto != []:
            risposta = f"Hai chiesto della pianta numero {num_richiesto}, ma attualmente nell'inquadratura vedo solo {seedling_count} piante."
        else:
            risposta = f"Ho scansionato la serra, ma attualmente non vedo alcuna pianta di '{specie_richiesta.capitalize()}' nell'inquadratura."
        
        # Reset del contesto (pianta non trovata)
        contesti_uscita = None
        if session:
            contesti_uscita = [{
                "name": f"{session}/contexts/analizzapianta-followup",
                "lifespanCount": 0,
                "parameters": {}
            }]
        return create_dialogflow_response(risposta, output_contexts=contesti_uscita)


def handle_saluto(parameters):
    """Gestisce un intento di saluto semplice."""
    req_data = request.get_json(silent=True)
    session = req_data.get("session") if req_data else None
    if session and session in session_memory:
        session_memory[session] = {
            "specie_vegetale": "",
            "number": ""
        }
        logger.info(f"[Memory Reset] Reimpostata session_memory per saluto in sessione: {session}")
    return create_dialogflow_response(
        "Ciao! Sono l'assistente Smart-Agri. Posso avviare l'analisi delle tue piante in tempo reale. Dimmi pure quando procedere!"
    )



def handle_consigli_specie(parameters):
    """
    Gestisce l'intento ConsigliSpecie.
    Identifica la pianta a cui si fa riferimento (dal contesto o dai parametri)
    e in base all'analisi cromatica (stato cromatico/ingiallimento) fornisce consigli mirati.
    """
    logger.info(f"Ricevuti parametri diretti: {parameters}")
    
    # 1. Recuperiamo l'intero payload inviato da Dialogflow
    req_data = request.get_json(silent=True)
    session = req_data.get("session") if req_data else None

    specie_raw = parameters.get("specie_vegetale", "")
    num_raw = parameters.get("number", "")
    supporto_raw = parameters.get("supporto", "")

    # Salva i valori specificati DIRETTAMENTE nella domanda corrente (prima di qualsiasi fallback)
    specie_diretta = specie_raw
    num_diretto = num_raw

    # Determina se l'utente ha specificato direttamente parametri nella domanda corrente
    specie_diretta_val = specie_diretta[0] if isinstance(specie_diretta, list) and specie_diretta else specie_diretta
    num_diretto_val = num_diretto[0] if isinstance(num_diretto, list) and num_diretto else num_diretto
    
    has_posizione_diretta = num_diretto_val is not None and num_diretto_val != ""
    has_specie_diretta = specie_diretta_val is not None and specie_diretta_val != "" and specie_diretta_val != [] and specie_diretta_val not in ("pianta", "piantina", "piantine")
    
    # Se ha specificato una posizione ma NON la specie, evitiamo il fallback per la specie.
    # Cerca direttamente la pianta in quella posizione.
    evita_fallback_specie = has_posizione_diretta and not has_specie_diretta

    # --- FALLBACK 1: Memoria interna di sessione (session_memory) ---
    memoria_sessione = session_memory.get(session, {}) if session else {}
    
    if not evita_fallback_specie and not specie_raw and "specie_vegetale" in memoria_sessione:
        specie_raw = memoria_sessione["specie_vegetale"]
        logger.info(f"[ConsigliSpecie] Recuperata specie da session_memory: '{specie_raw}'")
        
    if (num_raw is None or num_raw == "" or num_raw == []) and "number" in memoria_sessione:
        num_raw = memoria_sessione["number"]
        logger.info(f"[ConsigliSpecie] Recuperato numero pianta da session_memory: '{num_raw}'")

    # --- FALLBACK 2: Contesti di follow-up validi (se ancora vuoti) ---
    ctx_specie_salvata = ""
    if not specie_raw or (num_raw is None or num_raw == "" or num_raw == []):
        CONTESTI_PRIORITARI = [
            "analizzapianta-followup",
            "consiglispecie-followup",
            "wikispecie-followup",
        ]
        contesti = req_data.get("queryResult", {}).get("outputContexts", []) if req_data else []
        
        contesto_trovato = None
        for nome_target in CONTESTI_PRIORITARI:
            for ctx in contesti:
                ctx_name = ctx.get("name", "").lower()
                if f"/contexts/{nome_target}" in ctx_name:
                    contesto_trovato = ctx
                    break
            if contesto_trovato:
                break

        if contesto_trovato:
            parametri_contesto = contesto_trovato.get("parameters", {})
            ctx_specie_salvata = parametri_contesto.get("specie_vegetale", "")
            if isinstance(ctx_specie_salvata, list):
                ctx_specie_salvata = ctx_specie_salvata[0] if ctx_specie_salvata else ""
            ctx_specie_salvata = str(ctx_specie_salvata).lower().strip()
            
            if not evita_fallback_specie and not specie_raw:
                specie_raw = ctx_specie_salvata
                logger.info(f"[ConsigliSpecie] Specie dal contesto: '{specie_raw}'")
            if num_raw is None or num_raw == "" or num_raw == []:
                num_raw = parametri_contesto.get("number", "")
                if isinstance(num_raw, list):
                    num_raw = num_raw[0] if num_raw else ""
                logger.info(f"[ConsigliSpecie] Numero pianta dal contesto: '{num_raw}'")
    else:
        # Se abbiamo preso le info da session_memory, teniamo comunque traccia per il check di discrepanza
        ctx_specie_salvata = memoria_sessione.get("specie_vegetale", "")





    # Se l'utente ha specificato una specie DIVERSA da quella salvata nel contesto,
    # la posizione ereditata appartiene alla vecchia specie e va scartata.
    if specie_diretta and ctx_specie_salvata:
        specie_dir_norm = (specie_diretta[0] if isinstance(specie_diretta, list) and specie_diretta else str(specie_diretta)).lower().strip()
        ctx_sp_norm = (ctx_specie_salvata[0] if isinstance(ctx_specie_salvata, list) and ctx_specie_salvata else str(ctx_specie_salvata)).lower().strip()
        if specie_dir_norm and ctx_sp_norm and specie_dir_norm != ctx_sp_norm:
            num_raw = num_diretto  # Usa solo la posizione specificata direttamente
            logger.info(f"[ConsigliSpecie] Cambio specie rilevato ('{ctx_sp_norm}' → '{specie_dir_norm}'): posizione ereditata dal contesto scartata.")

    # 2. Normalizzazione parametri
    specie = specie_raw[0].lower() if isinstance(specie_raw, list) and specie_raw else str(specie_raw).lower()
    supporto = supporto_raw[0].lower() if isinstance(supporto_raw, list) and supporto_raw else str(supporto_raw).lower()
    
    plant_id = None
    if num_raw:
        try:
            plant_id = int(num_raw[0]) if isinstance(num_raw, list) else int(num_raw)
        except (ValueError, TypeError):
            pass

    # 3. Lettura analisi in tempo reale dalla cache
    if not analysis_cache.is_ready:
        return create_dialogflow_response(
            "Sto ancora completando l'analisi della serra. Riprova tra qualche secondo!"
        )
    analysis = analysis_cache.result
    plants = analysis.get("plants", [])

    # 4. Ricerca della pianta di riferimento
    pianta_riferimento = None
    
    # Se la specie è specificata
    if specie and specie not in ["pianta", "piantina", "piantine"]:
        piante_stesso_tipo = [p for p in plants if p["species"].lower() == specie]
        
        if len(piante_stesso_tipo) > 1:
            if not plant_id:
                # Chiedi chiarimenti a quale delle N piantine ci si riferisce
                posizioni_str = ", ".join(str(p["plant_id"]) for p in piante_stesso_tipo)
                risposta = f"Ci sono più piante di {specie.capitalize()} (nelle posizioni: {posizioni_str}). A quale di queste ti riferisci?"
                contesti_uscita = None
                if session:
                    # SALVA LA SPECIE NELLA MEMORIA DI SESSIONE PER IL PROSSIMO TURNO!
                    session_memory[session] = {
                        "specie_vegetale": specie,
                        "number": ""
                    }
                    logger.info(f"[Memory Save - Clarification] Salvata specie '{specie}' in attesa del numero.")
                    
                    contesti_uscita = [
                        {
                            "name": f"{session}/contexts/analizzapianta-followup",
                            "lifespanCount": 5,
                            "parameters": {
                                "specie_vegetale": specie
                            }
                        },
                        {
                            # Mantiene supporto nel contesto così il slot-filling
                            # lo trova già compilato quando l'utente risponde con il numero
                            "name": f"{session}/contexts/consiglispecie-followup",
                            "lifespanCount": 5,
                            "parameters": {
                                "supporto": supporto,
                                "specie_vegetale": specie
                            }
                        }
                    ]
                return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

            else:
                # Cerca quella specifica per la posizione (plant_id) indicata
                pianta_riferimento = next((p for p in piante_stesso_tipo if p["plant_id"] == plant_id), None)
                if not pianta_riferimento:
                    pianta_in_pos = next((p for p in plants if p["plant_id"] == plant_id), None)
                    posizioni_str = ", ".join(str(p["plant_id"]) for p in piante_stesso_tipo)
                    if pianta_in_pos:
                        risposta = f"In posizione {plant_id} non c'è una pianta di {specie.capitalize()}, ma c'è una pianta di {pianta_in_pos['species'].capitalize()}. Le piante di {specie.capitalize()} sono nelle posizioni: {posizioni_str}."
                    else:
                        risposta = f"Non esiste una pianta in posizione {plant_id}. Le piante di {specie.capitalize()} sono nelle posizioni: {posizioni_str}."
                    
                    contesti_uscita = None
                    if session:
                        contesti_uscita = [{
                            "name": f"{session}/contexts/analizzapianta-followup",
                            "lifespanCount": 5,
                            "parameters": {
                                "specie_vegetale": specie
                            }
                        }]
                    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
        elif len(piante_stesso_tipo) == 1:
            if plant_id:
                if piante_stesso_tipo[0]["plant_id"] == plant_id:
                    pianta_riferimento = piante_stesso_tipo[0]
                else:
                    pianta_in_pos = next((p for p in plants if p["plant_id"] == plant_id), None)
                    if pianta_in_pos:
                        risposta = f"In posizione {plant_id} non c'è una pianta di {specie.capitalize()}, ma c'è una pianta di {pianta_in_pos['species'].capitalize()}. L'unica pianta di {specie.capitalize()} è in posizione {piante_stesso_tipo[0]['plant_id']}."
                    else:
                        risposta = f"Non esiste una pianta in posizione {plant_id}. L'unica pianta di {specie.capitalize()} è in posizione {piante_stesso_tipo[0]['plant_id']}."
                    
                    contesti_uscita = None
                    if session:
                        contesti_uscita = [{
                            "name": f"{session}/contexts/analizzapianta-followup",
                            "lifespanCount": 5,
                            "parameters": {
                                "specie_vegetale": specie
                            }
                        }]
                    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
            else:
                pianta_riferimento = piante_stesso_tipo[0]
        else:
            # Nessuna pianta di questa specie trovata
            risposta = f"Non ho rilevato alcuna pianta di {specie.capitalize()} nella serra."
            contesti_uscita = None
            if session:
                contesti_uscita = [{
                    "name": f"{session}/contexts/analizzapianta-followup",
                    "lifespanCount": 0,
                    "parameters": {}
                }]
            return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

    # Se non c'è una specie specificata (richiesta generica)
    if not pianta_riferimento:
        if plant_id:
            pianta_riferimento = next((p for p in plants if p["plant_id"] == plant_id), None)
            if not pianta_riferimento:
                risposta = f"Non ho rilevato alcuna pianta in posizione {plant_id}."
                return create_dialogflow_response(risposta)
        elif plants:
            pianta_riferimento = plants[0]

    # 5. Determinazione stato e consigli mirati
    if pianta_riferimento:
        specie_reale = pianta_riferimento["species"].lower()
        anomaly_pct = pianta_riferimento["anomaly_pct"]
        id_pianta = pianta_riferimento["plant_id"]
        
        # Categorizzazione dello stato di salute
        if anomaly_pct > 70.0:
            stato = "critico"
        elif anomaly_pct > 40.0:
            stato = "anomalo"
        else:
            stato = "sano"
    else:
        # Fallback se non c'è nessuna pianta analizzata
        specie_reale = specie if (specie and specie not in ["pianta", "piantina", "piantine"]) else "altro"
        anomaly_pct = 0.0
        id_pianta = None
        stato = "sano"

    # Database dei consigli strutturato per Specie -> Stato -> Tema/Supporto
    consigli_db = {
        "pomodoro": {
            "critico": {
                "general": "Rimuovi immediatamente tutte le foglie e i rami gravemente ingialliti/secchi per fermare la diffusione di peronospora. Se il fusto principale è marcio, elimina l'intera pianta.",
                "acqua": "Sospendi l'irrigazione se il terreno è fradicio (sospetto marciume radicale), altrimenti irriga solo alla base senza bagnare il fogliame dopo aver rimosso le foglie marce.",
                "sole": "Il sole battente stressa ulteriormente la pianta compromessa. Ombreggiala temporaneamente con una rete finché non dà segni di ripresa.",
                "concime": "Non concimare! Il fertilizzante su radici danneggiate o sotto forte stress provocherebbe bruciature letali. Pota le parti malate prima di nutrire la pianta.",
                "preservare": "Isola o rimuovi le parti fortemente infette. Utilizza un trattamento rameico per proteggere i fusti e le foglie sane superstiti."
            },
            "anomalo": {
                "general": "La pianta mostra un ingiallimento moderato. Consigliamo di spostare il vaso a mezz'ombra o ombreggiare parzialmente la pianta e irrigare regolarmente.",
                "acqua": "Annaffia solo alla base al mattino presto, mantenendo il terreno umido ma senza ristagni. Aumenta la frequenza se il terreno è arido.",
                "sole": "Se esposta a sole cocente e temperature estreme, le foglie si stanno scottando. Spostala in una zona leggermente all'ombra nelle ore più calde.",
                "concime": "Applica un concime ricco di potassio e calcio per rinforzare le difese e prevenire il marciume apicale del pomodoro.",
                "preservare": "Rimuovi le prime foglie basse ingiallite e migliora il circolo d'aria intorno alla pianta per prevenire attacchi fungini."
            },
            "sano": {
                "general": "La pianta di pomodoro è in ottima salute. Continua così!",
                "acqua": "Mantieni un'irrigazione costante e regolare, preferibilmente nelle prime ore del mattino.",
                "sole": "Assicura un'esposizione in pieno sole (almeno 6 ore al giorno) per favorire la maturazione dei frutti.",
                "concime": "Applica un fertilizzante organico per orto ogni 15 giorni per sostenere la fioritura e fruttificazione.",
                "preservare": "Raccogli regolarmente i germogli ascellari (femminelle) per convogliare le energie sui rami principali."
            }
        },
        "basilico": {
            "critico": {
                "general": "Pota drasticamente tutti i fusti a circa 3 cm dalla terra per stimolare nuovi getti sani, o rimuovi la pianta se il fusto è nero e marcio (fusariosi).",
                "acqua": "Rinvasi d'emergenza se il terreno è saturo. Taglia via le parti necrotiche e non bagnare finché il terreno non è quasi asciutto.",
                "sole": "Sposta immediatamente il basilico all'ombra completa in un luogo fresco; il sole diretto ne accelererebbe il disseccamento totale.",
                "concime": "Evita qualsiasi concime che affaticherebbe le radici sofferenti. Pota e attendi la ricrescita all'ombra.",
                "preservare": "Elimina le foglie annerite o ammuffite e mantieni la pianta isolata per non diffondere spore fungine."
            },
            "anomalo": {
                "general": "Il basilico ha un principio di ingiallimento fogliare. Spostalo ad una parte più all'ombra, innaffialo maggiormente ed evita ristagni.",
                "acqua": "Irriga più spesso ma con quantità moderate. Il terreno deve rimanere fresco e umido come una spugna strizzata.",
                "sole": "Il sole diretto delle ore centrali è troppo forte. Colloca la pianta in una zona a mezz'ombra o luce filtrata.",
                "concime": "Somministra un concime azotato leggero per ridare colore verde brillante alle foglie stentate.",
                "preservare": "Cima le infiorescenze non appena compaiono per evitare che la pianta smetta di produrre foglie aromatiche."
            },
            "sano": {
                "general": "Il basilico è rigoglioso e sano.",
                "acqua": "Irriga regolarmente mantenendo il terreno costantemente umido ma ben drenato.",
                "sole": "Posizionalo in un luogo luminoso ma riparato dal sole battente del pomeriggio.",
                "concime": "Una leggera concimazione organica ogni 2-3 settimane supporterà la produzione di nuove foglie.",
                "preservare": "Raccogli le foglie cimando i rametti dall'alto per stimolare la crescita a cespuglio."
            }
        },
        "alloro": {
            "critico": {
                "general": "L'alloro è robusto; un danno >70% indica asfissia radicale grave o cocciniglia. Pota i rami secchi fino al legno sano e tratta con anticoccidico.",
                "acqua": "Sospendi subito le bagnature. Se in vaso, svasa la pianta e taglia le radici marce prima di rinvasare con terriccio nuovo ben drenato.",
                "sole": "Tieni la pianta in una zona ombreggiata e fresca per favorire il recupero dell'apparato radicale danneggiato.",
                "concime": "Nessun concime. Le piante di alloro debilitate non tollerano sali minerali in eccesso alle radici.",
                "preservare": "Rimuovi manualmente le cocciniglie residue usando un batuffolo di cotone imbevuto di alcol."
            },
            "anomalo": {
                "general": "L'alloro ha un leggero stress, probabilmente da clorosi o ristagno d'acqua. Spostalo in una zona moderatamente all'ombra ed evita annaffiature frequenti.",
                "acqua": "Bagna solo quando il terreno è completamente asciutto nei primi centimetri.",
                "sole": "Se la pianta è esposta a forte calore riflesso (es. contro un muro assolato), spostala in una posizione più fresca a mezz'ombra.",
                "concime": "Somministra del ferro chelato per contrastare l'ingiallimento fogliare tipico della clorosi ferrica.",
                "preservare": "Ispeziona la pagina inferiore delle foglie per escludere attacchi iniziali di parassiti."
            },
            "sano": {
                "general": "L'alloro è in perfetta salute.",
                "acqua": "Annaffia solo sporadicamente; tollera molto bene la siccità.",
                "sole": "Collocalo in pieno sole o a mezz'ombra a seconda dello spazio disponibile.",
                "concime": "Una manciata di stallatico in autunno o primavera è più che sufficiente.",
                "preservare": "Effettua potature di forma o contenimento all'inizio della primavera."
            }
        },
        "rosmarino": {
            "critico": {
                "general": "Il rosmarino soffre l'eccesso idrico. Se è al 70% di ingiallimento, le radici stanno marcendo: pota i rami secchi, rinvasa in terra sabbiosa e asciutta e non bagnare per due settimane.",
                "acqua": "Interrompi del tutto le annaffiature. Il rosmarino rischia di morire per asfissia se il terreno rimane bagnato.",
                "sole": "Posizionalo nel punto più soleggiato e ventilato possibile per far asciugare rapidamente il pane di terra.",
                "concime": "Non concimare. Il rosmarino predilige terreni poveri ed aridi; i nutrienti extra peggiorerebbero la situazione.",
                "preservare": "Rimuovi i rami privi di aghi e assicurati che i fori di drenaggio del vaso siano liberi."
            },
            "anomalo": {
                "general": "Il rosmarino mostra segni di stress da umidità. Sposta il vaso in pieno sole, riduci drasticamente l'acqua e controlla il drenaggio.",
                "acqua": "Annaffia pochissimo e solo quando la terra è secca da diversi giorni.",
                "sole": "Garantisci il massimo delle ore di sole diretto per stimolare la ripresa vegetativa.",
                "concime": "Non fertilizzare. Piuttosto, aggiungi della sabbia o argilla espansa al terreno per migliorare il drenaggio.",
                "preservare": "Usa vasi di terracotta che permettono una migliore traspirazione rispetto a quelli di plastica."
            },
            "sano": {
                "general": "Il rosmarino è in ottima salute.",
                "acqua": "Irriga solo in periodi di prolungata siccità e calore estremo.",
                "sole": "Mantieni la pianta esposta in pieno sole.",
                "concime": "Non necessita di alcuna concimazione.",
                "preservare": "Effettua leggere cimature per mantenere la pianta compatta ed evitare che il fusto si lignifichi eccessivamente."
            }
        },
        "altro": {
            "critico": {
                "general": "L'ingiallimento è severo. Pota drasticamente le parti morte o malate per favorire i nuovi germogli e riduci lo stress idrico.",
                "acqua": "Se la terra è inzuppata, sospendi le annaffiature e fai asciugare; se è arida, irriga abbondantemente ma senza ristagni.",
                "sole": "Sposta la pianta in una zona ombreggiata e fresca al riparo dal sole diretto finché non si stabilizza.",
                "concime": "Non concimare la pianta in stato di shock per evitare di bruciare l'apparato radicale compromesso.",
                "preservare": "Isola la pianta per evitare la diffusione di eventuali infezioni fungine o parassitarie."
            },
            "anomalo": {
                "general": "La pianta mostra un leggero ingiallimento. Spostala in una zona più all'ombra, irriga moderatamente al bisogno e valuta un concime leggero.",
                "acqua": "Regola l'irrigazione bagnando solo quando lo strato superficiale del terreno risulta asciutto al tatto.",
                "sole": "Evita il sole diretto nelle ore centrali; preferisci una posizione a mezz'ombra o con luce filtrata.",
                "concime": "Somministra un fertilizzante universale bilanciato a dosaggio dimezzato per stimolare la ripresa.",
                "preservare": "Elimina le foglie ingiallite per stimolare la pianta a produrre nuova vegetazione sana."
            },
            "sano": {
                "general": "La pianta è in buona salute.",
                "acqua": "Irriga secondo le necessità tipiche della pianta, evitando eccessi.",
                "sole": "Mantieni l'esposizione alla luce adatta alla tipologia di pianta.",
                "concime": "Applica un concime universale una volta al mese durante la stagione vegetativa.",
                "preservare": "Tieni pulito il fogliame e monitora periodicamente lo stato di salute generale."
            }
        }
    }

    # Risoluzione della specie per il database consigli
    chiave_specie = specie_reale if specie_reale in consigli_db else "altro"
    
    # Risoluzione del tema/supporto richiesto
    chiave_supporto = supporto if supporto in ["acqua", "sole", "concime", "preservare"] else "general"

    # Selezione del consiglio specifico
    consiglio = consigli_db[chiave_specie][stato][chiave_supporto]

    # Composizione risposta per l'utente
    prefisso = ""
    if id_pianta:
        prefisso = f"Consigli mirati per {specie_reale.capitalize()} (Posizione {id_pianta}):\n"
        prefisso += f"• Rilevato ingiallimento: {anomaly_pct}% (Stato: {stato.upper()})\n"
    else:
        prefisso = f"Consigli per {specie_reale.capitalize()}:\n"
        prefisso += f"• Stato di salute stimato: {stato.upper()}\n"
        
    if chiave_supporto != "general":
        prefisso += f"• Focus richiesto: {chiave_supporto.capitalize()}•\n"
        
    risposta = f"{prefisso}\n {consiglio}"

    # Aggiornamento del contesto per i follow-up successivi
    specie_da_salvare = specie_reale if (specie_reale and specie_reale != "altro") else ""
    # (session già estratta all'inizio della funzione)
    contesti_uscita = None
    if session:
        # Aggiorna la memoria di sessione interna con l'ultima pianta consigliata
        session_memory[session] = {
            "specie_vegetale": specie_da_salvare,
            "number": id_pianta if id_pianta else ""
        }
        logger.info(f"[Memory Save - Consigli] Salvato in sessione: {session_memory[session]}")
        
        contesti_uscita = [{
            "name": f"{session}/contexts/analizzapianta-followup",
            "lifespanCount": 5,
            "parameters": {
                "specie_vegetale": specie_da_salvare,
                "number": id_pianta if id_pianta else ""
            }
        }]


    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

def handle_wiki_specie(parameters):
    """
    Gestisce l'intento WikiSpecie.

    Parametri ricevuti da Dialogflow (definiti in WikiSpecie.json):
      - sezione_richiesta (@sezione_richiesta): la sezione wiki richiesta.
            Valori canonici: "nome scientifico" | "descrizione" | "coltivazione" | "usi"
      - azione (@azione): azione generica (spiega / dimmi di più / curiosità …)
            usata quando l'utente chiede info generali senza specificare la sezione.

    La specie vegetale NON è un parametro diretto dell'intento: viene ereditata
    dai contesti di input (analizzapianta-followup, analizzaserra-followup,
    consiglispecie-followup, wikispecie-followup) che Dialogflow injetta nel payload.

    Output:
      - Emette 'wikispecie-followup' (lifespan 10) per i follow-up successivi
        sulla stessa pianta.
      - Mantiene 'analizzapianta-followup' aggiornato per l'interoperabilità
        con gli altri intenti (ConsigliSpecie, AnalizzaPianta …).
    """
    req_data = request.get_json(silent=True)
    if not req_data:
        return create_dialogflow_response("Errore interno: payload mancante.")
    session = req_data.get("session", "")

    # ------------------------------------------------------------------
    # 1. Parametri diretti (sezione richiesta, azione e specie)
    # ------------------------------------------------------------------
    sezione_raw = parameters.get("sezione_richiesta", "")
    azione_raw  = parameters.get("azione", "")
    specie_raw_diretta = parameters.get("specie_vegetale", "")

    # Normalizzazione: Dialogflow può restituire lista o stringa
    sezione = (sezione_raw[0] if isinstance(sezione_raw, list) and sezione_raw else str(sezione_raw)).lower().strip()
    azione  = (azione_raw[0]  if isinstance(azione_raw,  list) and azione_raw  else str(azione_raw)).lower().strip()
    # Specie specificata direttamente dall'utente in questa domanda (es. "dammi info sul basilico")
    specie_diretta = (specie_raw_diretta[0] if isinstance(specie_raw_diretta, list) and specie_raw_diretta else str(specie_raw_diretta)).lower().strip()

    logger.info(f"[WikiSpecie] sezione='{sezione}', azione='{azione}', specie_diretta='{specie_diretta}'")

    # ------------------------------------------------------------------
    # 2. Recupero della specie (Priorità: Diretta -> queryText -> session_memory -> Contesto)
    # ------------------------------------------------------------------
    specie = ""
    
    # Priorità 1: Parametro diretto
    if specie_diretta and specie_diretta not in ("pianta", "piantina", "piantine"):
        specie = specie_diretta
        logger.info(f"[WikiSpecie] Specie impostata da parametro diretto: '{specie}'")

    # Priorità 2: Scansione del queryText (per match testuali diretti)
    if not specie:
        SPECIE_CONOSCIUTE = ["pomodoro", "basilico", "alloro", "rosmarino"]
        query_text = req_data.get("queryResult", {}).get("queryText", "").lower()
        for sp in SPECIE_CONOSCIUTE:
            if sp in query_text:
                specie = sp
                logger.info(f"[WikiSpecie] Specie estratta da queryText: '{specie}'")
                break

    # Priorità 3: Memoria interna di sessione
    if not specie:
        memoria_sessione = session_memory.get(session, {}) if session else {}
        if "specie_vegetale" in memoria_sessione and memoria_sessione["specie_vegetale"]:
            specie = memoria_sessione["specie_vegetale"]
            logger.info(f"[WikiSpecie] Specie recuperata da session_memory: '{specie}'")

    # Priorità 4: Contesti di input
    ctx_specie_salvata = ""
    CONTESTI_VALIDI = [
        "wikispecie-followup",
        "analizzapianta-followup",
        "analizzaserra-followup",
        "consiglispecie-followup",
    ]
    contesti_input = req_data.get("queryResult", {}).get("outputContexts", [])
    
    # Cerchiamo comunque la specie salvata nel contesto per confrontarla in caso di cambio
    for ctx in contesti_input:
        ctx_name = ctx.get("name", "").lower()
        if any(c in ctx_name for c in CONTESTI_VALIDI):
            param_ctx = ctx.get("parameters", {})
            valore = param_ctx.get("specie_vegetale", "")
            if isinstance(valore, list):
                valore = valore[0] if valore else ""
            valore = str(valore).lower().strip()
            if valore and valore not in ("pianta", "piantina", ""):
                ctx_specie_salvata = valore
                if not specie:
                    specie = valore
                    logger.info(f"[WikiSpecie] Specie recuperata dal contesto '{ctx_name}': '{specie}'")
                break

    if not specie:
        return create_dialogflow_response(
            "Non ho capito a quale pianta ti riferisci. Prova prima ad analizzarla con 'Come sta il pomodoro?' e poi chiedimi informazioni sulla wiki."
        )

    # ------------------------------------------------------------------
    # 3. Recupero informazioni dal database
    # ------------------------------------------------------------------
    if db_repository is None:
        return create_dialogflow_response("Il database della wiki non è attualmente raggiungibile.")

    info = db_repository.get_wiki_info(specie)
    if not info:
        return create_dialogflow_response(
            f"Mi dispiace, non ho ancora informazioni wiki su '{specie.capitalize()}'. "
            f"Le specie disponibili sono: Pomodoro, Basilico, Alloro, Rosmarino."
        )

    # ------------------------------------------------------------------
    # 4. Selezione del blocco di risposta in base alla sezione richiesta.
    #    Rendiamo il matching flessibile controllando sia i valori canonici
    #    sia le parole chiave (sinonimi) per evitare fallimenti dovuti a
    #    valori non normalizzati o liste di parametri.
    # ------------------------------------------------------------------
    # Categorie di parole chiave
    kw_nomenclatura = ["nome", "scientifico", "nomenclatura", "famiglia", "botanica", "chiama", "classificazione", "specie", "nome scientifico"]
    kw_descrizione  = ["descrizione", "portamento", "altezza", "origine", "cresce", "aspetto", "com'è", "ambiente", "vive", "dove", "provenienza"]
    kw_coltivazione = ["coltivazione", "esposizione", "acqua", "terreno", "temperatura", "clima", "sole", "luce", "innaffiare", "freddo", "cura", "coltiva", "piantare", "mantenere"]
    kw_usi          = ["usi", "uso", "usa", "tossic", "velen", "mangia", "commestibile", "proprietà", "serve", "utilizz", "cucina", "mangiare"]

    if azione in ["spiega", "spiegami", "curiosità", "curiosita", "parlami", "wiki", "dimmi di più", "dimmi di piu"]:
        tossicita = "Sì" if info['is_toxic'] else "No"
        risposta = (
            f"Wiki — {info['common_names']} ({info['scientific_name']})\n"
            f"Famiglia: {info['botanical_family']} | Origine: {info['origin_region']}\n"
            f"Altezza max: {info['max_height_cm']} cm | Tossicità: {tossicita}\n\n"
            f"Puoi chiedermi dettagli su: Nomenclatura, Descrizione, Coltivazione o Usi."
        )
    elif any(x in sezione for x in kw_nomenclatura):
        risposta = (
            f"Nomenclatura — {info['common_names']}\n"
            f"• Nome Scientifico: {info['scientific_name']}\n"
            f"• Famiglia Botanica: {info['botanical_family']}"
        )
    elif any(x in sezione for x in kw_descrizione):
        risposta = (
            f"Descrizione — {info['common_names']}\n"
            f"• Portamento: {info['plant_habit']}\n"
            f"• Altezza massima: {info['max_height_cm']} cm\n"
            f"• Regione d'origine: {info['origin_region']}"
        )
    elif any(x in sezione for x in kw_coltivazione):
        risposta = (
            f"Coltivazione — {info['common_names']}\n"
            f"• Esposizione: {info['sun_exposure']}\n"
            f"• Bisogno idrico: {info['water_needs']}\n"
            f"• Tipo di terreno: {info['soil_type']}\n"
            f"• Temperatura minima tollerata: {info['min_temp_celsius']} °C"
        )
    elif any(x in sezione for x in kw_usi):
        tossicita = "Sì" if info['is_toxic'] else "No"
        risposta = (
            f"Usi — {info['common_names']}\n"
            f"• Tossicità: {tossicita}\n"
            f"• Usi Principali: {info['primary_uses']}"
        )
    else:
        # Nessuna sezione specifica o azione generica (spiega / dimmi di più / curiosità)
        tossicita = "Sì" if info['is_toxic'] else "No"
        risposta = (
            f"Wiki — {info['common_names']} ({info['scientific_name']})\n"
            f"Famiglia: {info['botanical_family']} | Origine: {info['origin_region']}\n"
            f"Altezza max: {info['max_height_cm']} cm | Tossicità: {tossicita}\n\n"
            f"Puoi chiedermi dettagli su: Nomenclatura, Descrizione, Coltivazione o Usi."
        )

    # ------------------------------------------------------------------
    # 5. Emissione dei contesti di output per i follow-up
    #    - wikispecie-followup  (lifespan 10): segue il JSON originale
    #    - analizzapianta-followup (lifespan 5): interoperabilità con altri intenti
    # ------------------------------------------------------------------
    contesti_uscita = None
    if session:
        # Aggiorna la memoria di sessione interna con l'ultima specie della wiki consultata
        session_memory[session] = {
            "specie_vegetale": specie,
            "number": ""
        }
        logger.info(f"[Memory Save - Wiki] Salvato in sessione: {session_memory[session]}")
        
        contesti_uscita = [
            {
                "name": f"{session}/contexts/wikispecie-followup",
                "lifespanCount": 10,
                "parameters": {"specie_vegetale": specie}
            },
            {
                "name": f"{session}/contexts/analizzapianta-followup",
                "lifespanCount": 10,
                "parameters": {"specie_vegetale": specie}
            }
        ]

    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

# Mappa degli intenti registrati (Pattern Strategy)
# La chiave corrisponde al queryResult['intent']['displayName'] impostato su Dialogflow
INTENT_ROUTING = {
    "Default Welcome Intent": handle_saluto,
    "AnalizzaSerra":          handle_analizza_serra,
    "AnalizzaPianta":         handle_analizza_pianta,
    "ConsigliSpecie":         handle_consigli_specie,
    "WikiSpecie":             handle_wiki_specie
}

# =====================================================================
# ENDPOINT E ROUTING PRINCIPALE
# =====================================================================

@app.route('/')
def index():
    """Serve la dashboard web principale."""
    return send_from_directory('static', 'index.html')


@app.route('/latest-image')
def latest_image():
    """
    Restituisce l'ultima immagine annotata salvata dalla pipeline di analisi.
    Se non esiste ancora, restituisce un'immagine placeholder 404.
    """
    image_path = os.path.join(os.path.dirname(__file__), 'debug_annotated_plants.jpg')
    if not os.path.exists(image_path):
        # Prova a restituire la prima immagine di test come placeholder
        test_dir = os.path.join(os.path.dirname(__file__), 'test_images')
        images = [f for f in os.listdir(test_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if images:
            return send_file(os.path.join(test_dir, images[0]), mimetype='image/jpeg')
        return jsonify({'error': 'Nessuna immagine disponibile'}), 404

    # max_age=0: niente cache, il browser chiede sempre l'immagine aggiornata
    response = send_file(image_path, mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/db-data')
def get_db_data():
    """Restituisce le piante e le osservazioni registrate nel database."""
    if db_repository is None:
        return jsonify({
            'error': 'Database non inizializzato o non raggiungibile.',
            'plants': [],
            'observations': []
        }), 200 # Restituiamo 200 con liste vuote in modo che il frontend possa gestirlo con grazia

    try:
        plants = db_repository.get_all_plants()
        observations = db_repository.get_all_observations()
        return jsonify({
            'plants': plants,
            'observations': observations
        })
    except Exception as e:
        logger.error(f"Errore durante il recupero dei dati del database: {e}")
        return jsonify({
            'error': str(e),
            'plants': [],
            'observations': []
        }), 500


@app.route('/api/reset-and-analyze', methods=['POST'])
def reset_and_analyze():
    """Resetta il DB, forza un'analisi immediata e aggiorna la cache."""
    if db_repository is None or vision_service is None or camera_service is None:
        return jsonify({'error': 'Servizi non inizializzati.'}), 500
        
    try:
        # 1. Resetta il DB
        success = db_repository.reset_database()
        if not success:
            return jsonify({'error': 'Impossibile resettare il database.'}), 500
            
        # 2. Cattura e analizza (analisi forzata fuori dal ciclo del worker)
        frame = camera_service.capture_frame()
        analysis = vision_service.analyse_frame(frame)
        plants = analysis["plants"]
        seedling_count = analysis["seedling_count"]

        # 3. Aggiorna la cache con il risultato fresco
        analysis_cache.update(analysis)
        logger.info("[reset-and-analyze] Cache aggiornata con il nuovo risultato.")
        
        # 4. Salva le piante trovate
        if plants:
            db_repository.save_plants_from_serra(plants)
            
            # 5. Salva la prima osservazione per ogni pianta trovata
            for p in plants:
                db_repository.save_single_observation(
                    position=p["plant_id"],
                    health_status=p["health_status"],
                    anomaly_pct=p["anomaly_pct"],
                    seedling_count=seedling_count
                )
                
        return jsonify({'success': True, 'seedling_count': seedling_count})
    except Exception as e:
        logger.error(f"Errore durante il reset e ri-analisi: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-now', methods=['POST'])
def analyze_now():
    """Forza un'analisi immediata, aggiorna la cache e salva i dati nel DB senza resettarlo."""
    if db_repository is None or vision_service is None or camera_service is None:
        return jsonify({'error': 'Servizi non inizializzati.'}), 500
        
    try:
        # Pulisce la cache dell'immagine di test per costringere a sceglierne una nuova
        if hasattr(camera_service, 'cached_test_frame'):
            camera_service.cached_test_frame = None

        # 1. Cattura e analizza
        frame = camera_service.capture_frame()
        analysis = vision_service.analyse_frame(frame)
        plants = analysis["plants"]
        seedling_count = analysis["seedling_count"]

        # 2. Aggiorna la cache con il risultato fresco
        analysis_cache.update(analysis)
        logger.info("[analyze-now] Cache aggiornata con il nuovo risultato manuale.")
        
        # 3. Salva le piante trovate
        if plants:
            db_repository.save_plants_from_serra(plants)
            
            # 4. Salva l'osservazione per ogni pianta trovata
            for p in plants:
                db_repository.save_single_observation(
                    position=p["plant_id"],
                    health_status=p["health_status"],
                    anomaly_pct=p["anomaly_pct"],
                    seedling_count=seedling_count
                )
                
        return jsonify({'success': True, 'seedling_count': seedling_count})
    except Exception as e:
        logger.error(f"Errore durante l'analisi manuale: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache-status')
def cache_status():
    """Restituisce lo stato attuale della cache di analisi (utile per il debug)."""
    return jsonify({
        'is_ready': analysis_cache.is_ready,
        'age_seconds': analysis_cache.age_seconds,
        'worker_alive': background_worker.is_alive if background_worker else False,
        'message': analysis_cache.get_status_message()
    })


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
