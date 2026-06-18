import logging
import sys

def setup_logger():
    logger = logging.getLogger("smart_agri")
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        return logger
        
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()
