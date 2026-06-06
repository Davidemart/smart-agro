import time
import os
import cv2
import numpy as np
from config import Config
from utils.logger import logger

# Importa tensorflow e cvlib solo se necessario o gestisci eventuali assenze dei file dei modelli
# in modo da non bloccare lo startup di sviluppo
try:
    import tensorflow as tf
except ImportError:
    tf = None

try:
    import cvlib as cv
except ImportError:
    cv = None


class VisionService:
    def __init__(self):
        self.model_path = Config.KERAS_MODEL_PATH
        self.labels_path = Config.LABELS_PATH
        self.keras_model = None
        self.labels = []
        self.mock_mode = False

        # Inizializzazione modello Keras
        self._load_keras_model()
        
        # Inizializzazione YOLO (bootstrap e warm-up)
        self._warmup_yolo()

    def _load_keras_model(self):
        """Carica in memoria il modello Teachable Machine e le etichette."""
        if not os.path.exists(self.model_path) or not os.path.exists(self.labels_path):
            logger.warning(
                f"Modello Keras o etichette NON trovati alle posizioni specificate:\n"
                f"- Modello: {self.model_path}\n"
                f"- Etichette: {self.labels_path}\n"
                f"L'applicazione si avvierà in MOCK MODE per la classificazione delle piante."
            )
            self.mock_mode = True
            self.labels = ["Pomodoro", "Basilico", "Specie Sconosciuta"]
            return

        try:
            logger.info(f"Caricamento modello Keras da {self.model_path} in corso...")
            start_time = time.time()
            # Carica il modello Keras
            if tf is not None:
                # Disabilita i log prolissi di TensorFlow
                os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
                self.keras_model = tf.keras.models.load_model(self.model_path)
                logger.info(f"Modello Keras caricato con successo in {int((time.time() - start_time) * 1000)}ms.")
            else:
                logger.error("Libreria TensorFlow non installata. Avvio in Mock Mode.")
                self.mock_mode = True

            # Carica le etichette
            with open(self.labels_path, "r", encoding="utf-8") as f:
                self.labels = [line.strip().split(" ", 1)[-1] for line in f.readlines()]
            logger.info(f"Etichette caricate: {self.labels}")
            
        except Exception as e:
            logger.error(f"Errore critico durante il caricamento del modello Keras: {e}")
            logger.warning("Avvio in Mock Mode a causa di errore di caricamento.")
            self.mock_mode = True
            self.labels = ["Pomodoro", "Basilico", "Specie Sconosciuta"]

    def _warmup_yolo(self):
        """Esegue una inferenza a vuoto su YOLO per forzare il bootstrap iniziale dei pesi."""
        if cv is None:
            logger.warning("cvlib non è installato. Il conteggio delle piantine sarà simulato.")
            return

        logger.info("Inizio warm-up YOLO per ridurre la latenza della prima richiesta...")
        try:
            # Crea un'immagine nera fittizia 100x100
            dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
            # Esegue una rilevazione veloce
            cv.detect_common_objects(dummy_frame)
            logger.info("Warm-up YOLO completato con successo.")
        except Exception as e:
            logger.error(f"Errore durante il warm-up di YOLO: {e}. I pesi potrebbero essere scaricati alla prima richiesta reale.")

    def _check_timeout(self, start_time, phase_name):
        """Verifica se il tempo totale trascorso supera la soglia critica di 3500ms."""
        elapsed = (time.time() - start_time) * 1000
        if elapsed > 3500:
            logger.warning(f"TIMEOUT CRITICO: La pipeline ha superato il budget temporale dopo {phase_name} ({elapsed:.2f}ms).")
            raise TimeoutError(f"Pipeline interrotta a causa di latenza eccessiva durante: {phase_name}")

    def analyse_frame(self, frame):
        """
        Esegue la pipeline di analisi multimodale sul frame:
        Fase A: Classificazione specie
        Fase B: Segmentazione anomalie cromatiche
        Fase C: Rilevamento piantine YOLO
        Restituisce un dizionario con i risultati e solleva TimeoutError in caso di latenza > 3500ms.
        """
        pipeline_start = time.time()
        logger.info("Avvio della pipeline di analisi dell'immagine...")

        # Risultati di default in caso di anomalie
        results = {
            "species": "Specie Non Identificata",
            "confidence": 0.0,
            "anomaly_pct": 0.0,
            "seedling_count": 0,
            "health_status": "Stato Sano"
        }

        # ==========================================
        # FASE A: Classificazione Specie Vegetale (Keras)
        # ==========================================
        phase_a_start = time.time()
        try:
            if self.mock_mode:
                # Simulazione per test in assenza del file del modello
                results["species"] = "Basilico"
                results["confidence"] = 0.85
                logger.info("[FASE A - MOCK] Classificato come Basilico (conf: 85%)")
            else:
                # 1. Manipolazione Geometrica (224x224)
                resized = cv2.resize(frame, (224, 224), interpolation=cv2.INTER_AREA)
                
                # 2. Normalizzazione pixel [-1, 1] (Teachable Machine standard)
                normalized = (resized.astype(np.float32) / 127.5) - 1.0
                
                # Aggiunge dimensione batch (1, 224, 224, 3)
                input_tensor = np.expand_dims(normalized, axis=0)
                
                # 3. Inferenza
                predictions = self.keras_model.predict(input_tensor)
                best_class_idx = np.argmax(predictions[0])
                confidence = float(predictions[0][best_class_idx])
                
                # Assegna i risultati
                results["confidence"] = confidence
                
                # Gestione Soglia di Confidenza Insufficiente (< 60%)
                if confidence < 0.60:
                    results["species"] = "Specie Non Identificata"
                    logger.warning(f"Confidenza Keras troppo bassa ({confidence * 100:.1f}%). Specie impostata a 'Specie Non Identificata'")
                else:
                    results["species"] = self.labels[best_class_idx]
                    logger.info(f"Classificato come: {results['species']} con confidenza {confidence*100:.1f}%")
        except Exception as e:
            logger.error(f"Errore durante la classificazione Keras: {e}")
            results["species"] = "Specie Non Identificata"
            results["confidence"] = 0.0

        keras_time = int((time.time() - phase_a_start) * 1000)
        logger.info(f"Latenza Inferenza Keras: {keras_time}ms")
        
        # Controllo timeout
        self._check_timeout(pipeline_start, "Classificazione Keras")

        # ==========================================
        # FASE B: Segmentazione Anomalie Cromatiche (OpenCV)
        # ==========================================
        phase_b_start = time.time()
        try:
            # 1. Conversione Spazio Colore da BGR a HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # 2. Isolamento Spettrale (Ingiallimento/Clorosi)
            lower_yellow = np.array([15, 40, 40], dtype=np.uint8)
            upper_yellow = np.array([30, 255, 255], dtype=np.uint8)
            
            # 3. Generazione Maschera Binaria
            mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
            
            # 4. Operazioni Morfologiche di Pulizia
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            # Apertura (elimina piccoli punti di rumore isolati)
            mask_cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            # Chiusura (colma i vuoti interni delle macro-aree degenerate)
            mask_final = cv2.morphologyEx(mask_cleaned, cv2.MORPH_CLOSE, kernel)
            
            # 5. Quantificazione Clorotica
            total_pixels = frame.shape[0] * frame.shape[1]
            white_pixels = cv2.countNonZero(mask_final)
            anomaly_pct = float((white_pixels / total_pixels) * 100.0)
            
            results["anomaly_pct"] = round(anomaly_pct, 2)
            
            # Logica dei Fallback: Soglia Critica di Stress (> 30%)
            if anomaly_pct > 30.0:
                results["health_status"] = "Critico - Rilevato forte ingiallimento"
                logger.warning(f"Rilevata percentuale di ingiallimento critica: {anomaly_pct:.2f}%")
            else:
                results["health_status"] = "Sano"
                logger.info(f"Pianta in stato sano. Area ingiallita: {anomaly_pct:.2f}%")
                
        except Exception as e:
            logger.error(f"Errore durante la segmentazione OpenCV: {e}")
            results["anomaly_pct"] = 0.0
            results["health_status"] = "Errore Analisi Fogliare"

        opencv_time = int((time.time() - phase_b_start) * 1000)
        logger.info(f"Latenza Segmentazione OpenCV: {opencv_time}ms")
        
        # Controllo timeout
        self._check_timeout(pipeline_start, "Segmentazione OpenCV")

        # ==========================================
        # FASE C: Rilevamento e Conteggio Piantine (YOLO / cvlib)
        # ==========================================
        phase_c_start = time.time()
        try:
            if cv is None:
                # Fallback se non è installata la libreria cvlib
                results["seedling_count"] = 2
                logger.info("[FASE C - MOCK] Conteggio piantine simulato a 2.")
            else:
                # 1. Rilevamento degli oggetti comuni
                bbox, label, conf = cv.detect_common_objects(frame)
                
                # 2. e 3. Filtro per classe target "potted plant" o "plant"
                # Le etichette di COCO contengono "potted plant" per le piante in vaso
                plant_detections = [l for l in label if l in ["potted plant", "plant"]]
                
                # 4. Calcolo della cardinalità
                results["seedling_count"] = len(plant_detections)
                logger.info(f"YOLO ha rilevato {results['seedling_count']} piantine nel frame. Dettaglio: {plant_detections}")
                
        except Exception as e:
            logger.error(f"Errore durante l'Object Detection YOLO: {e}")
            results["seedling_count"] = 0

        yolo_time = int((time.time() - phase_c_start) * 1000)
        logger.info(f"Latenza Object Detection YOLO: {yolo_time}ms")

        # Latenza totale
        total_time = int((time.time() - pipeline_start) * 1000)
        logger.info(f"Latenza Totale Pipeline VisionService: {total_time}ms")
        
        return results
