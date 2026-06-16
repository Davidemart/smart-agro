import cv2
import os
import random
import numpy as np
from config import Config
from utils.logger import logger

class CameraService:
    def __init__(self):
        self.camera_index = Config.CAMERA_INDEX
        self.test_images_dir = "test_images"
        
        # Verifica se l'indice della camera è una stringa che richiede la modalità test
        self.is_test_mode = False
        self.cached_test_frame = None
        if isinstance(self.camera_index, str) and (self.camera_index.lower() == "test" or self.camera_index.lower() == "test_images"):
            self.is_test_mode = True
            logger.info("Modalità telecamera impostata su TEST (caricamento immagini locali).")
            self._ensure_test_images_dir()

    def _ensure_test_images_dir(self):
        """Assicura l'esistenza della cartella test_images."""
        if not os.path.exists(self.test_images_dir):
            os.makedirs(self.test_images_dir)
            logger.info(f"Creata la cartella '{self.test_images_dir}' per i dataset di test.")
            
        # Controlla se la cartella contiene almeno un'immagine valida
        images = [f for f in os.listdir(self.test_images_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if not images:
            logger.error(f"La cartella '{self.test_images_dir}' è vuota. Impossibile procedere con i test offline.")
            raise FileNotFoundError(
                f"La cartella '{self.test_images_dir}' non contiene immagini di test (.jpg, .jpeg, .png). "
                f"Inserisci almeno un'immagine per poter eseguire i test offline senza webcam."
            )


    def capture_frame(self):
        """
        Acquisisce un frame. 
        Se in modalità test (o se l'apertura della webcam fallisce), carica un'immagine casuale dalla cartella test_images.
        Altrimenti acquisisce il frame in tempo reale dalla webcam.
        """
        if self.is_test_mode:
            return self._load_random_test_image()

        logger.info(f"Tentativo di acquisizione frame dalla webcam all'indice {self.camera_index}")
        cap = cv2.VideoCapture(self.camera_index)
        
        if not cap.isOpened():
            logger.warning(f"Impossibile aprire la webcam all'indice {self.camera_index}. Avvio automatico del caricamento da dataset di test.")
            self.is_test_mode = True
            self._ensure_test_images_dir()
            return self._load_random_test_image()

        try:
            # Pulisci il buffer scartando i primi frame
            for _ in range(5):
                cap.grab()
                
            ret, frame = cap.read()
            
            if not ret or frame is None:
                logger.warning("Lettura frame fallita dalla webcam fisica. Passaggio a caricamento da dataset di test.")
                self.is_test_mode = True
                self._ensure_test_images_dir()
                return self._load_random_test_image()
                
            logger.info(f"Frame acquisito da webcam fisica. Dimensioni: {frame.shape[1]}x{frame.shape[0]}")
            
            # --- AGGIUNTA PER IL DEBUG ---
            # Salva l'immagine catturata NELLA CARTELLA PRINCIPALE
            debug_path = "debug_last_capture.jpg"
            cv2.imwrite(debug_path, frame)
            logger.info(f"Immagine di debug salvata come '{debug_path}'")
            # -----------------------------
            
            return frame

        finally:
            cap.release()
            logger.info("Risorsa webcam fisica rilasciata.")

    def _load_random_test_image(self):
        """Carica un'immagine a caso dalla cartella test_images e la restituisce."""
        if self.cached_test_frame is not None:
            logger.info("[TEST MODE] Utilizzo frame di test in cache.")
            return self.cached_test_frame
            
        self._ensure_test_images_dir()
        images = [f for f in os.listdir(self.test_images_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        
        if not images:
            raise FileNotFoundError("Nessuna immagine di test disponibile nella cartella test_images.")
            
        selected_image_name = random.choice(images)
        selected_image_path = os.path.join(self.test_images_dir, selected_image_name)
        
        logger.info(f"[TEST MODE] Caricamento immagine di test dal dataset: '{selected_image_path}'")
        frame = cv2.imread(selected_image_path)
        
        if frame is None:
            raise RuntimeError(f"Impossibile leggere l'immagine di test '{selected_image_path}' con OpenCV.")
            
        self.cached_test_frame = frame
        return frame

