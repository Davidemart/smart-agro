import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))
    
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_USER = os.getenv("DB_USER", "academy_user") 
    DB_PASSWORD = os.getenv("DB_PASSWORD", "PasswordSicura123!") 
    DB_NAME = os.getenv("DB_NAME", "smart_agri")
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", 5))
    
    KERAS_MODEL_PATH = os.getenv("KERAS_MODEL_PATH", "models/keras_model.h5")
    LABELS_PATH = os.getenv("LABELS_PATH", "models/labels.txt")
    
    _camera_idx = os.getenv("CAMERA_INDEX", "0")
    CAMERA_INDEX = int(_camera_idx) if str(_camera_idx).isdigit() else _camera_idx
