import logging
import sys

def setup_logger():
    """Configura il logger."""
    logger = logging.getLogger("smart_agri")
    logger.setLevel(logging.INFO)
    
    # Se il logger ha già degli handler, non aggiungerne altri per evitare log duplicati
    if logger.handlers:
        return logger
        
    # Formato di log personalizzato con precisione al millisecondo
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

# Istanza del logger condivisa
logger = setup_logger()
