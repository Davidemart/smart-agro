import mysql.connector
from mysql.connector import pooling, Error
from config import Config
from utils.logger import logger

class DBRepository:
    _pool = None

    @classmethod
    def initialize_pool(cls):
        """Inizializza il connection pool per MySQL una sola volta."""
        if cls._pool is None:
            try:
                logger.info(f"Inizializzazione del MySQL connection pool (host: {Config.DB_HOST}, user: {Config.DB_USER}, pool_size: {Config.DB_POOL_SIZE})")
                cls._pool = pooling.MySQLConnectionPool(
                    pool_name="smart_agri_pool",
                    pool_size=Config.DB_POOL_SIZE,
                    pool_reset_session=True,
                    host=Config.DB_HOST,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    database=Config.DB_NAME
                )
                logger.info("MySQL connection pool inizializzato con successo.")
            except Error as e:
                logger.error(f"Errore critico nell'inizializzazione del connection pool MySQL: {e}")
                # Non solleviamo l'eccezione qui per consentire al server Flask di avviarsi
                # e registrare gli errori di runtime o gestire la riconnessione successiva
                cls._pool = None

    def __init__(self):
        # Assicura che il pool sia inizializzato
        if self._pool is None:
            self.initialize_pool()

    def _get_connection(self):
        """Prende una connessione dal pool."""
        if self._pool is None:
            # Riprova ad inizializzare se in precedenza ha fallito
            self.initialize_pool()
            if self._pool is None:
                raise Error("Il database non è configurato o non è raggiungibile.")
        return self._pool.get_connection()

    def get_or_create_plant(self, cursor, species_name):
        """
        Recupera il plant_id per una data specie o la inserisce se non esiste.
        Utilizza un cursore già aperto all'interno di una transazione attiva.
        """
        # Prepared statements / query parametrizzata
        select_query = "SELECT plant_id FROM plants WHERE name = %s"
        cursor.execute(select_query, (species_name,))
        result = cursor.fetchone()
        
        if result:
            return result[0]
        
        # Se non esiste, la inseriamo
        insert_query = "INSERT INTO plants (name) VALUES (%s)"
        cursor.execute(insert_query, (species_name,))
        return cursor.lastrowid

    def save_observation(self, species_name, health_status, anomaly_pct, seedling_count):
        """
        Salva un'osservazione legata a una specie vegetale.
        Implementa atomicità multi-tabella (ACID) con Rollback.
        """
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            # Disabilita l'autocommit per gestire la transazione manualmente
            connection.autocommit = False
            cursor = connection.cursor()

            # 1. Recupero o inserimento specie nella tabella 'plants'
            plant_id = self.get_or_create_plant(cursor, species_name)
            logger.info(f"Ottenuto plant_id={plant_id} per la specie '{species_name}'")

            # 2. Inserimento metriche nella tabella 'observations'
            insert_obs_query = """
                INSERT INTO observations (plant_id, health_status, anomaly_pct, seedling_count)
                VALUES (%s, %s, %s, %s)
            """
            obs_data = (plant_id, health_status, anomaly_pct, seedling_count)
            cursor.execute(insert_obs_query, obs_data)
            
            # Conferma la transazione per entrambe le operazioni
            connection.commit()
            logger.info(f"Salvataggio osservazione completato con successo nel DB per plant_id={plant_id}.")
            return True
            
        except Error as e:
            logger.error(f"Errore durante l'operazione sul database: {e}")
            if connection:
                try:
                    logger.warning("Esecuzione rollback della transazione SQL a causa del fallimento.")
                    connection.rollback()
                except Error as rollback_err:
                    logger.error(f"Errore durante il rollback: {rollback_err}")
            raise e
            
        finally:
            # Rilascio rigoroso delle risorse
            if cursor:
                try:
                    cursor.close()
                except Exception as e:
                    logger.error(f"Errore nella chiusura del cursore: {e}")
            if connection:
                try:
                    connection.close() # Restituisce la connessione al pool
                    logger.info("Connessione restituita al pool con successo.")
                except Exception as e:
                    logger.error(f"Errore nella chiusura della connessione: {e}")
