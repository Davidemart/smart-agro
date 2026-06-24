import threading
import time
from utils.logger import logger


class AnalysisCache:
    """
    Cache thread-safe per l'ultimo risultato di analisi prodotto dal worker in background.
    Utilizza un RLock per garantire la consistenza in ambienti multi-thread (Flask + worker).
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._result = None          # Ultimo risultato di analyse_frame()
        self._timestamp = None       # Epoch time dell'ultimo aggiornamento
        self._is_ready = False       # True dopo il primo aggiornamento riuscito

    # ------------------------------------------------------------------
    # Scrittura (chiamata dal background worker)
    # ------------------------------------------------------------------

    def update(self, result: dict):
        """Salva il nuovo risultato di analisi e aggiorna il timestamp."""
        with self._lock:
            self._result = result
            self._timestamp = time.time()
            self._is_ready = True
        logger.info(
            f"[Cache] Aggiornata: {result.get('seedling_count', 0)} piantine rilevate "
            f"alle {time.strftime('%H:%M:%S', time.localtime(self._timestamp))}."
        )

    # ------------------------------------------------------------------
    # Lettura (chiamata dagli handler Flask/Dialogflow)
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """Restituisce True se la cache contiene almeno un risultato valido."""
        with self._lock:
            return self._is_ready

    @property
    def result(self) -> dict | None:
        """Restituisce l'ultimo risultato di analisi (o None se non ancora disponibile)."""
        with self._lock:
            return self._result

    @property
    def age_seconds(self) -> float | None:
        """Restituisce quanti secondi fa è stato aggiornato il risultato."""
        with self._lock:
            if self._timestamp is None:
                return None
            return time.time() - self._timestamp

    def get_status_message(self) -> str:
        """Messaggio leggibile sullo stato della cache (utile per il logging)."""
        with self._lock:
            if not self._is_ready:
                return "Cache non ancora pronta (prima analisi in corso)."
            age = self.age_seconds
            return f"Cache pronta. Ultimo aggiornamento: {age:.1f}s fa."


# Istanza singleton condivisa tra worker e handlers
analysis_cache = AnalysisCache()
