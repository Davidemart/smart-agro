import time
import os
import cv2
import numpy as np
from config import Config
from utils.logger import logger

try:
    import tf_keras as keras
except ImportError:
    try:
        import tensorflow.keras as keras
    except ImportError:
        keras = None

try:
    import tensorflow as tf
except ImportError:
    tf = None


class VisionService:
    def __init__(self):
        self.model_path = Config.KERAS_MODEL_PATH
        self.labels_path = Config.LABELS_PATH
        self.keras_model = None
        self.labels = []
        self.mock_mode = False

        # Configurazione percorsi locali YOLOv4
        self.yolo_cfg = "models/yolo/yolov4.cfg"
        self.yolo_weights = "models/yolo/yolov4.weights"
        self.yolo_labels_path = "models/yolo/coco.names"
        self.yolo_net = None
        self.yolo_classes = []

        # Inizializzazione modello Keras
        self._load_keras_model()
        
        # Inizializzazione YOLOv4 locale con OpenCV DNN
        self._load_yolo_model()

    def _load_keras_model(self):
        """Carica in memoria il modello Teachable Machine e le etichette."""
        if not os.path.exists(self.model_path) or not os.path.exists(self.labels_path):
            logger.warning(
                f"Modello Keras o etichette NON trovati. Avvio in MOCK MODE per Keras."
            )
            self.mock_mode = True
            self.labels = ["Pomodoro", "Basilico", "Specie Sconosciuta"]
            return

        try:
            logger.info(f"Caricamento modello Keras da {self.model_path} in corso...")
            start_time = time.time()
            if keras is not None:
                os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
                self.keras_model = keras.models.load_model(self.model_path)
                logger.info(f"Modello Keras caricato con successo in {int((time.time() - start_time) * 1000)}ms.")
            else:
                logger.error("Libreria Keras/TensorFlow non installata. Avvio in Mock Mode.")
                self.mock_mode = True

            with open(self.labels_path, "r", encoding="utf-8") as f:
                self.labels = [line.strip().split(" ", 1)[-1] for line in f.readlines()]
            logger.info(f"Etichette caricate: {self.labels}")
            
        except Exception as e:
            logger.error(f"Errore critico durante il caricamento del modello Keras: {e}")
            self.mock_mode = True
            self.labels = ["Pomodoro", "Basilico", "Specie Sconosciuta"]

    def _load_yolo_model(self):
        """Carica il modello YOLOv4 locale tramite il modulo DNN di OpenCV ed esegue il warm-up."""
        if not os.path.exists(self.yolo_cfg) or not os.path.exists(self.yolo_weights) or not os.path.exists(self.yolo_labels_path):
            logger.warning("File YOLOv4 non trovati in models/yolo/. Il conteggio delle piantine sarà simulato.")
            return

        logger.info("Caricamento modello YOLOv4 locale da models/yolo/...")
        try:
            # Carica le classi di COCO
            with open(self.yolo_labels_path, "r") as f:
                self.yolo_classes = [line.strip() for line in f.readlines()]

            # Inizializza la rete neurale Darknet in OpenCV
            self.yolo_net = cv2.dnn.readNetFromDarknet(self.yolo_cfg, self.yolo_weights)
            self.yolo_net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.yolo_net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

            # Warm-up obbligatorio con frame idoneo (416x416) per allocare la memoria
            dummy_frame = np.zeros((416, 416, 3), dtype=np.uint8)
            blob = cv2.dnn.blobFromImage(dummy_frame, 1/255.0, (416, 416), swapRB=True, crop=False)
            self.yolo_net.setInput(blob)
            self.yolo_net.forward(self.yolo_net.getUnconnectedOutLayersNames())
            logger.info("YOLOv4 locale caricato e Warm-up completato con successo.")
        except Exception as e:
            logger.error(f"Errore durante il caricamento o warm-up di YOLOv4: {e}")
            self.yolo_net = None

    def _check_timeout(self, start_time, phase_name):
        """Verifica se il tempo totale trascorso supera la soglia critica di 3500ms."""
        elapsed = (time.time() - start_time) * 1000
        if elapsed > 3500:
            logger.warning(f"TIMEOUT CRITICO: La pipeline ha superato il budget temporale dopo {phase_name} ({elapsed:.2f}ms).")
            raise TimeoutError(f"Pipeline interrotta a causa di latenza eccessiva durante: {phase_name}")

    def analyse_frame(self, frame):
        """Pipeline di analisi multimodale orientata alla singola pianta."""
        pipeline_start = time.time()
        logger.info("Avvio della pipeline di analisi dell'immagine aggiornata (Ritaglio per singola pianta)...")

        # Struttura dati di output aggiornata
        results = {
            "seedling_count": 0,
            "plants": []
        }

        # ==========================================
        # FASE 1: Object Detection (YOLOv4 nativo)
        # ==========================================
        phase_1_start = time.time()
        boxes = []
        try:
            if self.yolo_net is None:
                logger.info("[FASE 1 - MOCK] YOLO non disponibile. Simulazione: considero l'intero frame come 1 pianta.")
                h, w = frame.shape[:2]
                boxes = [[0, 0, w, h]]
            else:
                h, w = frame.shape[:2]
                blob = cv2.dnn.blobFromImage(frame, 1/255.0, (416, 416), swapRB=True, crop=False)
                self.yolo_net.setInput(blob)
                layer_outputs = self.yolo_net.forward(self.yolo_net.getUnconnectedOutLayersNames())
                
                temp_boxes, confidences = [], []
                
                for output in layer_outputs:
                    for detection in output:
                        scores = detection[5:]
                        class_id = np.argmax(scores)
                        confidence = scores[class_id]
                        
                        if confidence > 0.4:
                            class_name = self.yolo_classes[class_id]
                            if class_name in ["pottedplant", "potted plant", "plant"]:
                                centerX, centerY = int(detection[0] * w), int(detection[1] * h)
                                width, height = int(detection[2] * w), int(detection[3] * h)
                                x, y = int(centerX - width / 2), int(centerY - height / 2)
                                
                                temp_boxes.append([x, y, int(width), int(height)])
                                confidences.append(float(confidence))
                
                # Applicazione Non-Maximum Suppression (NMS)
                indices = cv2.dnn.NMSBoxes(temp_boxes, confidences, 0.40, 0.3)
                if len(indices) > 0:
                    for i in indices.flatten():
                        boxes.append(temp_boxes[i])

            results["seedling_count"] = len(boxes)
            logger.info(f"YOLO ha rilevato {results['seedling_count']} piantine. Latenza: {int((time.time() - phase_1_start) * 1000)}ms")
                
        except Exception as e:
            logger.error(f"Errore durante l'Object Detection YOLO: {e}. Fallback a intero frame.")
            h, w = frame.shape[:2]
            boxes = [[0, 0, w, h]]
            results["seedling_count"] = 1

        self._check_timeout(pipeline_start, "Object Detection YOLO")

       # ==========================================
        # FASE 2, 3 e 4: Cropping, Keras e OpenCV per singola pianta
        # ==========================================
        
        # 1. Creiamo una copia del frame originale su cui disegnare i riquadri
        annotated_frame = frame.copy()

        # Ordina le bounding box da sinistra a destra (in base alla coordinata x)
        boxes.sort(key=lambda b: b[0])
        logger.info(f"Coordinate box ordinate da sinistra a destra: {boxes}")

        for idx, (x, y, w_box, h_box) in enumerate(boxes):
            # Limite massimo di analisi per evitare timeout su Dialogflow
            if idx >= 5:
                logger.warning("Raggiunto il limite massimo di 5 piantine elaborabili per evitare timeout.")
                break

            # Assicuriamoci che le coordinate di crop non escano dai bordi
            h_frame, w_frame = frame.shape[:2]
            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(w_frame, x + w_box), min(h_frame, y + h_box)

            if x2 <= x1 or y2 <= y1:
                continue # Salta crop non validi

            crop = frame[y1:y2, x1:x2]
            
            plant_data = {
                "plant_id": idx + 1,
                "species": "Specie Non Identificata",
                "confidence": 0.0,
                "anomaly_pct": 0.0,
                "health_status": "Sano"
            }

            # --- Classificazione Specie (Keras) sul Crop ---
            try:
                if self.mock_mode:
                    plant_data["species"] = "Basilico"
                    plant_data["confidence"] = 0.85
                else:
                    # 1. Converti da BGR (OpenCV) a RGB (richiesto da Keras)
                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                    
                    # 2. Ridimensionamento a (224, 224)
                    crop_resized = cv2.resize(crop_rgb, (224, 224))
                    
                    # 3. Trasforma in array numpy e aggiungi dimensione batch
                    crop_array = np.asarray(crop_resized, dtype=np.float32)
                    crop_array = np.expand_dims(crop_array, axis=0)
                    
                    # 4. Normalizzazione standard Teachable Machine: [-1, 1]
                    crop_array = (crop_array / 127.5) - 1.0
                    
                    # 5. Predizione esatta con verbose=0 come nel test
                    predictions = self.keras_model.predict(crop_array, verbose=0)[0]
                    best_class_idx = np.argmax(predictions)
                    confidence = float(predictions[best_class_idx])
                    
                    plant_data["confidence"] = confidence
                    if confidence >= 0.50: # Qui puoi abbassare a 0 se vuoi vedere sempre la classe
                        plant_data["species"] = self.labels[best_class_idx]
            except Exception as e:
                logger.error(f"Errore Keras su pianta {idx+1}: {e}")

            # --- Segmentazione Anomalie (OpenCV) sul Crop ---
            #try:
            #    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            #    lower_yellow = np.array([15, 40, 40], dtype=np.uint8)
            #    upper_yellow = np.array([30, 255, 255], dtype=np.uint8)
            #    mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
            #    
            #    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            #    mask_cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            #    mask_final = cv2.morphologyEx(mask_cleaned, cv2.MORPH_CLOSE, kernel)
            #    
            #    total_pixels = crop.shape[0] * crop.shape[1]
            #    white_pixels = cv2.countNonZero(mask_final)
            #    
            #    if total_pixels > 0:
            #        anomaly_pct = float((white_pixels / total_pixels) * 100.0)
            #        plant_data["anomaly_pct"] = round(anomaly_pct, 2)
            #        if anomaly_pct > 30.0:
            #            plant_data["health_status"] = "Critico - Rilevato forte ingiallimento"
            #except Exception as e:
            #    logger.error(f"Errore OpenCV su pianta {idx+1}: {e}")

            # Salvataggio dati singola pianta e check timeout
            results["plants"].append(plant_data)
            logger.info(f"Analisi Pianta {idx+1} completata: {plant_data['species']}, {plant_data['anomaly_pct']}% anomalia.")
            
            # --- NOVITÀ: Disegno su Immagine ---
            # Scegliamo il colore (Verde se sana, Rosso se critica)
            #color = (0, 0, 255) if plant_data["anomaly_pct"] > 30.0 else (0, 255, 0)

            #per ora è sempre verde
            color = (0, 255, 0)
            
            # Disegna il rettangolo
            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
            
            # Disegna l'etichetta con ID (posizione) e specie
            label = f"P{plant_data['plant_id']}: {plant_data['species']}"
            cv2.putText(annotated_frame, label, (x1, max(20, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            
            self._check_timeout(pipeline_start, f"Analisi Pianta {idx+1}")

        # ==========================================
        # FINE CICLO: Mostra e Salva l'immagine finale
        # ==========================================
        if len(boxes) > 0:
            try:
                # Salva l'immagine nella cartella del progetto
                cv2.imwrite("debug_annotated_plants.jpg", annotated_frame)
                
                # Apre la finestra non bloccante (si aggiorna ad ogni richiesta)
                cv2.imshow("Smart-Agri: Rilevamento Piante", annotated_frame)
                cv2.waitKey(1) # 1 millisecondo di attesa per permettere a OpenCV di renderizzare la finestra senza bloccare Flask
            except Exception as e:
                logger.error(f"Errore durante il rendering della finestra OpenCV: {e}")

        logger.info(f"Latenza Totale Pipeline: {int((time.time() - pipeline_start) * 1000)}ms")
        
        return results