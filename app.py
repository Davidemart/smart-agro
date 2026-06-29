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
            interval_seconds=10
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

    # Controllo di sicurezza sull'ereditarietà dei parametri da contesto
    req_data = request.get_json(silent=True)
    query_text = ""
    if req_data and "queryResult" in req_data:
        query_text = req_data["queryResult"].get("queryText", "").lower()

    # Parole chiave generiche e specifiche nel testo dell'utente
    generic_keywords = ["pianta", "piante", "piantina", "piantine", "coltura", "colture", "serra", "generale", "tutti", "tutte"]
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
        chiede_conteggio = "quant" in azione or ("conta" in azione and "controll" not in azione)

        req_data = request.get_json(silent=True)
        session = req_data.get("session") if req_data else None

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

    # Reset del contesto
    req_data = request.get_json(silent=True)
    session = req_data.get("session") if req_data else None
    contesti_uscita = None
    if session:
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
    # 1. Estrazione dei due possibili parametri da Dialogflow
    num_richiesto = parameters.get("number", "")
    specie_richiesta = parameters.get("specie_vegetale", "")

    # Normalizzazione specie richiesta
    if isinstance(specie_richiesta, list) and specie_richiesta:
        specie_richiesta = str(specie_richiesta[0]).lower()
    else:
        specie_richiesta = str(specie_richiesta).lower()

    # Se non c'è né il numero né la specie, chiediamo chiarimenti
    if not num_richiesto and (not specie_richiesta or specie_richiesta == "pianta"):
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
    
    if num_richiesto:
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
                    req_data = request.get_json(silent=True)
                    session = req_data.get("session") if req_data else None
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
            
            req_data = request.get_json(silent=True)
            session = req_data.get("session") if req_data else None
            
            contesti_uscita = None
            if session:
                contesti_uscita = [{
                    "name": f"{session}/contexts/analizzapianta-followup",
                    "lifespanCount": 5,
                    "parameters": {
                        "specie_vegetale": specie_richiesta
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
        
        # Salviamo la specie nel contesto di Dialogflow per le domande successive
        req_data = request.get_json(silent=True)
        session = req_data.get("session")
        
        contesti_uscita = [{
            "name": f"{session}/contexts/analizzapianta-followup",
            "lifespanCount": 5,
            "parameters": {
                "specie_vegetale": pianta_trovata['species'].lower()
            }
        }]
        
        return create_dialogflow_response(risposta, output_contexts=contesti_uscita)
    else:
        # Messaggio di cortesia se la pianta richiesta non è presente nell'inquadratura
        if num_richiesto:
            risposta = f"Hai chiesto della pianta numero {num_richiesto}, ma attualmente nell'inquadratura vedo solo {seedling_count} piante."
        else:
            risposta = f"Ho scansionato la serra, ma attualmente non vedo alcuna pianta di '{specie_richiesta.capitalize()}' nell'inquadratura."
        
        # Reset del contesto
        req_data = request.get_json(silent=True)
        session = req_data.get("session") if req_data else None
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
        },
        "rosmarino": {
            "acqua": "Il rosmarino tollera molto bene la siccità. Annaffia solo quando il terreno è completamente asciutto, evitando assolutamente i ristagni.",
            "sole": "Ama le esposizioni in pieno sole e ben arieggiate.",
            "concime": "È poco esigente. Basta una leggera concimazione organica all'inizio della primavera o in autunno.",
            "preservare": "Per prevenire i marciumi radicali (il suo peggior nemico), assicurati che il vaso o il terreno abbiano un drenaggio eccellente."
        },
        "altro": {
            "acqua": "In generale, prima di annaffiare, tocca sempre il terreno: dai acqua solo se i primi centimetri sono asciutti per evitare marciumi e asfissia radicale.",
            "sole": "Assicura una buona illuminazione, preferibilmente luce diffusa. Se non conosci la specie, evita il sole diretto nelle ore più calde.",
            "concime": "Puoi usare un concime universale bilanciato durante il periodo primaverile ed estivo, seguendo sempre le dosi minime consigliate.",
            "preservare": "Mantieni la pianta pulita rimuovendo eventuali foglie secche. Osserva periodicamente il fusto e le foglie per intercettare per tempo la comparsa di parassiti."
        }
    }

    # Teniamo traccia della specie reale da salvare nel contesto prima di sovrascriverla con "altro" per la wiki
    specie_da_salvare = specie if (specie and specie != "pianta" and specie != "altro") else None

    # Se la specie non c'è, o non è presente nel dizionario, usiamo i consigli generici
    if not specie or specie not in wiki:
        specie = "altro"

    # Risposta dinamica basata sui parametri
    if specie in wiki and supporto in wiki[specie]:
        risposta = f"🌿 Consigli per {specie.capitalize()} (Tema: {supporto}):\n{wiki[specie][supporto]}"
    elif supporto:
        risposta = f"In generale, per quanto riguarda '{supporto}', assicurati sempre di non esagerare per non stressare la pianta. Hai bisogno di dettagli su una specie in particolare come pomodoro o basilico?"
    else:
        risposta = "Non ho capito esattamente quale consiglio ti serve. Prova a chiedermi dell'acqua, del sole o del concime per una specifica pianta."

    # Aggiunta/aggiornamento del contesto per conservare la memoria della pianta analizzata
    session = req_data.get("session") if req_data else None
    contesti_uscita = None
    if session and specie_da_salvare:
        contesti_uscita = [{
            "name": f"{session}/contexts/analizzapianta-followup",
            "lifespanCount": 5,
            "parameters": {
                "specie_vegetale": specie_da_salvare
            }
        }]

    return create_dialogflow_response(risposta, output_contexts=contesti_uscita)

def handle_wiki_specie(parameters):
    """
    Gestisce l'intento WikiSpecie.
    Interroga il database per fornire informazioni dettagliate sulla specie richiesta.
    """
    specie_raw = parameters.get("specie_vegetale", "")
    sezione_raw = parameters.get("sezione_richiesta", "")
    
    # 1. Recuperiamo l'intero payload inviato da Dialogflow
    req_data = request.get_json(silent=True)
    
    if not specie_raw or specie_raw == "pianta":
        contesti = req_data.get("queryResult", {}).get("outputContexts", [])
        for ctx in contesti:
            if "analizzapianta-followup" in ctx.get("name", "").lower():
                parametri_contesto = ctx.get("parameters", {})
                if "specie_vegetale" in parametri_contesto:
                    specie_raw = parametri_contesto["specie_vegetale"]
                    break

    specie = specie_raw[0].lower() if isinstance(specie_raw, list) and specie_raw else str(specie_raw).lower()
    sezione = sezione_raw[0].lower() if isinstance(sezione_raw, list) and sezione_raw else str(sezione_raw).lower()

    if not specie:
        return create_dialogflow_response("Di quale pianta vuoi conoscere le informazioni sulla wiki?")

    if db_repository is None:
        return create_dialogflow_response("Il database della wiki non è attualmente raggiungibile.")

    info = db_repository.get_wiki_info(specie)
    
    if not info:
        return create_dialogflow_response(f"Mi dispiace, ma non ho ancora informazioni dettagliate su '{specie.capitalize()}' nella mia wiki.")

    # Formattazione risposta a blocchi
    if "nomenclatura" in sezione or "nome" in sezione or "famiglia" in sezione:
        risposta = (
            f"📖 **Nomenclatura per {info['common_names']}**:\n"
            f"• Nome Scientifico: {info['scientific_name']}\n"
            f"• Famiglia Botanica: {info['botanical_family']}"
        )
    elif "descrizione" in sezione or "cresce" in sezione or "origine" in sezione:
        risposta = (
            f"📖 **Descrizione per {info['common_names']}**:\n"
            f"• Portamento: {info['plant_habit']}\n"
            f"• Altezza Massima: {info['max_height_cm']} cm\n"
            f"• Regione d'Origine: {info['origin_region']}"
        )
    elif "coltivazione" in sezione or "acqua" in sezione or "sole" in sezione or "freddo" in sezione or "terreno" in sezione:
        risposta = (
            f"📖 **Coltivazione per {info['common_names']}**:\n"
            f"• Esposizione: {info['sun_exposure']}\n"
            f"• Bisogno Idrico: {info['water_needs']}\n"
            f"• Tipo di Terreno: {info['soil_type']}\n"
            f"• Temperatura Minima tollerata: {info['min_temp_celsius']} °C"
        )
    elif "usi" in sezione or "veleno" in sezione or "serve" in sezione or "uso" in sezione or "tossic" in sezione:
        tossicita = "Sì" if info['is_toxic'] else "No"
        risposta = (
            f"📖 **Usi per {info['common_names']}**:\n"
            f"• Tossicità: {tossicita}\n"
            f"• Usi Principali: {info['primary_uses']}"
        )
    else:
        # Risposta generica se la sezione non è specificata chiaramente
        risposta = (
            f"📖 **Wiki: {info['common_names']}** ({info['scientific_name']})\n"
            f"Famiglia: {info['botanical_family']} | Origine: {info['origin_region']}.\n"
            f"Cosa vuoi sapere in particolare? (Nomenclatura, Descrizione, Coltivazione, Usi)"
        )

    # Conserviamo la specie in memoria
    session = req_data.get("session") if req_data else None
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

# Mappa degli intenti registrati (Pattern Strategy)
# La chiave corrisponde al queryResult['intent']['displayName'] impostato su Dialogflow
INTENT_ROUTING = {
    "Default Welcome Intent": handle_saluto,
    "AnalizzaSerra": handle_analizza_serra,
    "AnalizzaPianta": handle_analizza_pianta,
    "ConsigliSpecie": handle_consigli_specie,
    "WikiSpecie": handle_wiki_specie
    #"ProblemiPianta": handle_problemi_pianta  
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
