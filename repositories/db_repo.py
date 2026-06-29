import time
import mysql.connector
from mysql.connector import pooling, Error
from config import Config
from utils.logger import logger

class DBRepository:
    _pool = None
    _last_init_attempt = 0
    _init_cooldown = 60 # Cooldown di 60 secondi tra i tentativi di inizializzazione falliti

    @classmethod
    def initialize_pool(cls):
        """Inizializza il connection pool per MySQL una sola volta."""
        current_time = time.time()
        if cls._pool is None:
            # Salta il tentativo di connessione se siamo all'interno della finestra di cooldown
            if current_time - cls._last_init_attempt < cls._init_cooldown:
                logger.warning("Inizializzazione del pool MySQL saltata per evitare blocchi (cooldown attivo).")
                return
            
            cls._last_init_attempt = current_time
            try:
                logger.info(f"Inizializzazione del MySQL connection pool (host: {Config.DB_HOST}, user: {Config.DB_USER}, pool_size: {Config.DB_POOL_SIZE})")
                cls._pool = pooling.MySQLConnectionPool(
                    pool_name="smart_agri_pool",
                    pool_size=Config.DB_POOL_SIZE,
                    pool_reset_session=True,
                    host=Config.DB_HOST,
                    user=Config.DB_USER,
                    password=Config.DB_PASSWORD,
                    database=Config.DB_NAME,
                    connect_timeout=2 # Timeout veloce di 2 secondi
                )
                logger.info("MySQL connection pool inizializzato con successo.")
            except Error as e:
                logger.error(f"Errore critico nell'inizializzazione del connection pool MySQL: {e}")
                # Non solleviamo l'eccezione qui per consentire al server Flask di avviarsi
                # e registrare gli errori di runtime o gestire la riconnessione successiva
                cls._pool = None

    def __init__(self):
        """Assicura che il pool sia inizializzato e prepara i dati wiki."""
        if self._pool is None:
            self.initialize_pool()
        self.initialize_wiki_data()

    def _get_connection(self):
        """Prende una connessione dal pool."""
        if self._pool is None:
            # Riprova ad inizializzare se in precedenza ha fallito
            self.initialize_pool()
            if self._pool is None:
                raise Error("Il database non è configurato o non è raggiungibile.")
        return self._pool.get_connection()

    def save_plants_from_serra(self, plants_data):
        """
        [AnalizzaSerra]
        Salva o aggiorna la mappa della serra nella tabella 'plants'.
        Usa 'ON DUPLICATE KEY UPDATE' così se la pianta in quella posizione 
        era già stata registrata, ne aggiorna semplicemente la specie riconosciuta.
        """
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            connection.autocommit = False
            cursor = connection.cursor()

            # La query inserisce la posizione e il nome. Se la posizione (UNIQUE) 
            # esiste già, aggiorna il nome della specie.
            upsert_query = """
                INSERT INTO plants (position, name) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE name = VALUES(name)
            """
            
            for p in plants_data:
                # p["plant_id"] del dizionario Python corrisponde alla 'position' di YOLO
                cursor.execute(upsert_query, (p["plant_id"], p["species"]))

            connection.commit()
            logger.info("Tabella 'plants' aggiornata con successo dalla scansione della serra.")
            return True
            
        except Error as e:
            logger.error(f"Errore durante l'aggiornamento della tabella plants: {e}")
            if connection:
                connection.rollback()
            raise e
        finally:
            if cursor: cursor.close()
            if connection: connection.close()


    def save_single_observation(self, position, health_status, anomaly_pct, seedling_count):
        """
        [AnalizzaPianta]
        Salva un'osservazione nella tabella 'observations' per una specifica posizione.
        """
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            connection.autocommit = False
            cursor = connection.cursor()

            # 1. Recupera il plant_id (PK) reale basato sulla 'position' richiesta dall'utente
            cursor.execute("SELECT plant_id FROM plants WHERE position = %s", (position,))
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"Nessuna pianta trovata alla posizione {position}. Assicurati di aver fatto prima AnalizzaSerra.")
                return False
                
            real_plant_id = result[0]

            # 2. Inserisce le metriche nella tabella observations
            insert_obs_query = """
                INSERT INTO observations (plant_id, health_status, anomaly_pct, seedling_count)
                VALUES (%s, %s, %s, %s)
            """
            cursor.execute(insert_obs_query, (real_plant_id, health_status, anomaly_pct, seedling_count))
            
            connection.commit()
            logger.info(f"Osservazione salvata con successo per la posizione {position} (plant_id reale={real_plant_id}).")
            return True
            
        except Error as e:
            logger.error(f"Errore salvataggio osservazione: {e}")
            if connection:
                connection.rollback()
            raise e
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def get_all_plants(self):
        """Recupera tutte le piante registrate nel database con mapping manuale."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT plant_id, position, name, DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at FROM plants ORDER BY position ASC")
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Error as e:
            logger.error(f"Errore recupero piante dal DB: {e}")
            return []
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def get_all_observations(self):
        """Recupera tutte le osservazioni registrate nel database con mapping manuale."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            cursor.execute("SELECT obs_id, plant_id, health_status, anomaly_pct, seedling_count, DATE_FORMAT(observed_at, '%Y-%m-%d %H:%i:%s') as observed_at FROM observations ORDER BY observed_at DESC")
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Error as e:
            logger.error(f"Errore recupero osservazioni dal DB: {e}")
            return []
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def reset_database(self):
        """Svuota completamente le tabelle plants e observations e resetta gli AUTO_INCREMENT."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            connection.autocommit = False
            cursor = connection.cursor()
            
            # Disable foreign key checks to truncate tables with relationships
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
            cursor.execute("TRUNCATE TABLE observations;")
            cursor.execute("TRUNCATE TABLE plants;")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
            
            connection.commit()
            logger.info("Database resettato con successo (tabelle svuotate).")
            return True
        except Error as e:
            logger.error(f"Errore durante il reset del database: {e}")
            if connection:
                connection.rollback()
            return False
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def initialize_wiki_data(self):
        """Popola la tabella wiki_species se vuota."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM wiki_species")
            count = cursor.fetchone()[0]
            
            if count == 0:
                logger.info("Popolamento tabella wiki_species con dati predefiniti...")
                insert_query = """
                    INSERT INTO wiki_species (
                        scientific_name, common_names, botanical_family, 
                        plant_habit, max_height_cm, origin_region, 
                        sun_exposure, water_needs, soil_type, min_temp_celsius, 
                        is_toxic, primary_uses
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                data = [
                    ('Solanum lycopersicum', 'Pomodoro', 'Solanaceae', 'Erbacea', 200, 'America Centrale', 'Pieno sole', 'Elevata', 'Drenante e ricco', 10.0, False, 'Culinario'),
                    ('Ocimum basilicum', 'Basilico', 'Lamiaceae', 'Erbacea', 60, 'Asia tropicale', 'Pieno sole o mezz\'ombra', 'Elevata', 'Drenante', 10.0, False, 'Culinario, Officinale'),
                    ('Laurus nobilis', 'Alloro', 'Lauraceae', 'Arbusto/Albero', 1000, 'Bacino del Mediterraneo', 'Pieno sole o mezz\'ombra', 'Moderata', 'Drenante', -5.0, False, 'Culinario, Ornamentale'),
                    ('Salvia rosmarinus', 'Rosmarino', 'Lamiaceae', 'Arbusto', 200, 'Bacino del Mediterraneo', 'Pieno sole', 'Bassa', 'Drenante e arido', -10.0, False, 'Culinario, Officinale, Ornamentale')
                ]
                cursor.executemany(insert_query, data)
                connection.commit()
                logger.info("Tabella wiki_species popolata con successo.")
        except Error as e:
            logger.error(f"Errore durante il popolamento di wiki_species: {e}")
            if connection:
                connection.rollback()
        finally:
            if cursor: cursor.close()
            if connection: connection.close()

    def get_wiki_info(self, species):
        """Recupera le informazioni di wiki_species per un determinato nome comune o scientifico."""
        connection = None
        cursor = None
        try:
            connection = self._get_connection()
            cursor = connection.cursor(dictionary=True)
            
            query = """
                SELECT * FROM wiki_species 
                WHERE LOWER(common_names) LIKE %s OR LOWER(scientific_name) LIKE %s
            """
            search_term = f"%{species.lower()}%"
            cursor.execute(query, (search_term, search_term))
            result = cursor.fetchone()
            return result
        except Error as e:
            logger.error(f"Errore durante il recupero delle info wiki per {species}: {e}")
            return None
        finally:
            if cursor: cursor.close()
            if connection: connection.close()