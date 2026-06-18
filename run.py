from app import app
from config import Config
from utils.logger import logger

if __name__ == '__main__':
    logger.info(f"Avvio del server Smart-Agri in modalità '{Config.FLASK_ENV}'...")
    app.run(
        host='0.0.0.0', 
        port=Config.FLASK_PORT, 
        debug=(Config.FLASK_ENV == 'development'),
        use_reloader=False
    )
