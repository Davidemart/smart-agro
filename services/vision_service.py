import time
import os
import cv2
import numpy as np
from config import Config
from utils.logger import logger

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
            if tf is not None:
                os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
                self.keras_model = tf.keras.models.load_model(self.model_path)
                logger.info(f"Modello Keras caricato con successo in {int((time.time() - start_time) * 1000)}ms.")
            else:
                logger.error("Libreria TensorFlow non installata. Avvio in Mock Mode.")
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
        """Pipeline di analisi multimodale sul frame."""
        pipeline_start = time.time()
        logger.info("Avvio della pipeline di analisi dell'immagine...")

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
                results["species"] = "Basilico"
                results["confidence"] = 0.85
                logger.info("[FASE A - MOCK] Classificato come Basilico (conf: 85%)")
            else:
                resized = cv2.resize(frame, (224, 224), interpolation=cv2.INTER_AREA)
                normalized = (resized.astype(np.float32) / 127.5) - 1.0
                input_tensor = np.expand_dims(normalized, axis=0)
                
                predictions = self.keras_model.predict(input_tensor)
                best_class_idx = np.argmax(predictions[0])
                confidence = float(predictions[0][best_class_idx])
                
                results["confidence"] = confidence
                if confidence < 0.60:
                    results["species"] = "Specie Non Identificata"
                else:
                    results["species"] = self.labels[best_class_idx]
                    logger.info(f"Classificato come: {results['species']} ({confidence*100:.1f}%)")
        except Exception as e:
            logger.error(f"Errore durante la classificazione Keras: {e}")

        logger.info(f"Latenza Inferenza Keras: {int((time.time() - phase_a_start) * 1000)}ms")
        self._check_timeout(pipeline_start, "Classificazione Keras")

        # ==========================================
        # FASE B: Segmentazione Anomalie Cromatiche (OpenCV)
        # ==========================================
        phase_b_start = time.time()
        try:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            lower_yellow = np.array([15, 40, 40], dtype=np.uint8)
            upper_yellow = np.array([30, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
            
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask_cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask_final = cv2.morphologyEx(mask_cleaned, cv2.MORPH_CLOSE, kernel)
            
            total_pixels = frame.shape[0] * frame.shape[1]
            white_pixels = cv2.countNonZero(mask_final)
            anomaly_pct = float((white_pixels / total_pixels) * 100.0)
            
            results["anomaly_pct"] = round(anomaly_pct, 2)
            if anomaly_pct > 30.0:
                results["health_status"] = "Critico - Rilevato forte ingiallimento"
            else:
                results["health_status"] = "Sano"
        except Exception as e:
            logger.error(f"Errore durante la segmentazione OpenCV: {e}")

        logger.info(f"Latenza Segmentazione OpenCV: {int((time.time() - phase_b_start) * 1000)}ms")
        self._check_timeout(pipeline_start, "Segmentazione OpenCV")

        # ==========================================
        # FASE C: Rilevamento Piantine (YOLOv4 nativo con OpenCV)
        # ==========================================
        phase_c_start = time.time()
        try:
            if self.yolo_net is None:
                results["seedling_count"] = 2
                logger.info("[FASE C - MOCK] Conteggio piantine simulato a 2.")
            else:
                h, w = frame.shape[:2]
                # Generazione blob standard per YOLO (416x416)
                blob = cv2.dnn.blobFromImage(frame, 1/255.0, (416, 416), swapRB=True, crop=False)
                self.yolo_net.setInput(blob)
                layer_outputs = self.yolo_net.forward(self.yolo_net.getUnconnectedOutLayersNames())
                
                boxes, confidences, class_ids = [], [], []
                
                for output in layer_outputs:
                    for detection in output:
                        scores = detection[5:]
                        class_id = np.argmax(scores)
                        confidence = scores[class_id]
                        
                        # Soglia di confidenza al 50%
                        if confidence > 0.5:
                            class_name = self.yolo_classes[class_id]
                            # Filtro per contare solo piante o piante in vaso (Dataset COCO)
                            if class_name in ["potted plant", "plant"]:
                                box = detection[0:4] * np.array([w, h, w, h])
                                (centerX, centerY, width, height) = box.astype("int")
                                x = int(centerX - (width / 2))
                                y = int(centerY - (height / 2))
                                
                                boxes.append([x, y, int(width), int(height)])
                                confidences.append(float(confidence))
                                class_ids.append(class_id)
                
                # Applicazione Non-Maximum Suppression (NMS) per evitare doppi conteggi
                indices = cv2.dnn.NMSBoxes(boxes, confidences, 0.5, 0.4)
                results["seedling_count"] = len(np.array(indices).flatten()) if len(indices) > 0 else 0
                logger.info(f"YOLO nativo ha rilevato {results['seedling_count']} piantine nel frame.")
                
        except Exception as e:
            logger.error(f"Errore durante l'Object Detection YOLOv4 nativo: {e}")
            results["seedling_count"] = 0

        logger.info(f"Latenza YOLOv4: {int((time.time() - phase_c_start) * 1000)}ms")
        logger.info(f"Latenza Totale Pipeline: {int((time.time() - pipeline_start) * 1000)}ms")
        
        return results