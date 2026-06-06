import os
from dotenv import load_dotenv

# Carica il file .env se presente
load_dotenv()

class Config:
    # Flask settings
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
    
    # DB settings
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "smart_agri")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", 5))
    
    # Model settings
    KERAS_MODEL_PATH = os.getenv("KERAS_MODEL_PATH", "models/keras_model.h5")
    LABELS_PATH = os.getenv("LABELS_PATH", "models/labels.txt")
    
    # Camera settings
    CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", 0))
