import threading
import time
from utils.logger import logger


class BackgroundWorker:
    """
    Worker daemon che esegue la pipeline di analisi (webcam → YOLO → Keras)
    in background a intervalli regolari, aggiornando l'AnalysisCache.

    In questo modo gli handler di Dialogflow leggono sempre un risultato
    già pronto, rispondendo in meno di 100ms invece di attendere l'intera
    pipeline (che su CPU può superare i 5 secondi imposti da Dialogflow).
    """

    def __init__(self, camera_service, vision_service, cache, interval_seconds: int = 10):
        """
        Args:
            camera_service:   Istanza di CameraService già inizializzata.
            vision_service:   Istanza di VisionService già inizializzata.
            cache:            Istanza di AnalysisCache condivisa con gli handler.
            interval_seconds: Quanti secondi attendere tra un'analisi e la successiva.
        """
        self._camera_service = camera_service
        self._vision_service = vision_service
        self._cache = cache
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SmartAgri-BackgroundWorker",
            daemon=True  # Il thread si ferma automaticamente quando Flask termina
        )

    # ------------------------------------------------------------------
    # Ciclo principale
    # ------------------------------------------------------------------

    def _run_loop(self):
        logger.info(
            f"[Worker] Thread avviato. Analisi ogni {self._interval}s. "
            f"Prima esecuzione immediata..."
        )
        while not self._stop_event.is_set():
            self._run_once()
            # Attendi l'intervallo configurato (interrompibile via stop())
            self._stop_event.wait(timeout=self._interval)

    def _run_once(self):
        """Esegue un singolo ciclo di cattura + analisi e aggiorna la cache."""
        try:
            logger.info("[Worker] Avvio ciclo di analisi in background...")
            start = time.time()

            frame = self._camera_service.capture_frame()
            result = self._vision_service.analyse_frame(frame)

            elapsed_ms = int((time.time() - start) * 1000)
            logger.info(f"[Worker] Ciclo completato in {elapsed_ms}ms. Aggiorno la cache.")
            self._cache.update(result)

        except TimeoutError as te:
            logger.warning(f"[Worker] Timeout durante l'analisi (ignorato, riprovo al prossimo ciclo): {te}")
        except Exception as e:
            logger.error(f"[Worker] Errore imprevisto durante il ciclo di analisi: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Avvio / Arresto
    # ------------------------------------------------------------------

    def start(self):
        """Avvia il thread di background."""
        self._thread.start()
        logger.info("[Worker] Thread di background registrato e avviato.")

    def stop(self):
        """Segnala al thread di fermarsi alla prossima iterazione."""
        self._stop_event.set()
        logger.info("[Worker] Segnale di stop inviato al thread di background.")

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()
