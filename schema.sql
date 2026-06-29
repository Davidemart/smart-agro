-- Creazione del Database (se non esistente)
CREATE DATABASE IF NOT EXISTS smart_agri;
USE smart_agri;

-- Tabella Plants
CREATE TABLE IF NOT EXISTS plants (
    plant_id INT AUTO_INCREMENT PRIMARY KEY,
    position INT NOT NULL UNIQUE,   
    name VARCHAR(100) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tabella Observations
CREATE TABLE IF NOT EXISTS observations (
    obs_id INT AUTO_INCREMENT PRIMARY KEY,
    plant_id INT NOT NULL,
    health_status VARCHAR(100),
    anomaly_pct FLOAT,
    seedling_count INT,
    observed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (plant_id) REFERENCES plants(plant_id) ON DELETE CASCADE
);

-- Tabella wiki_species
CREATE TABLE IF NOT EXISTS wiki_species (
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    -- Nomenclatura e Tassonomia
    scientific_name VARCHAR(150) NOT NULL UNIQUE,
    common_names TEXT,
    botanical_family VARCHAR(100),
    
    -- Descrizione Botanica e Habitat
    plant_habit VARCHAR(50),      -- Es: Albero, Arbusto, Rampicante, Erbacea
    max_height_cm INT,            -- Altezza massima stimata
    origin_region VARCHAR(100),   -- Es: America Centrale, Bacino del Mediterraneo
    
    -- Coltivazione
    sun_exposure VARCHAR(50),     -- Es: Pieno sole, Mezz'ombra, Ombra
    water_needs VARCHAR(50),      -- Es: Bassa, Moderata, Elevata
    soil_type VARCHAR(100),       -- Es: Drenante, Acido, Argilloso
    min_temp_celsius DECIMAL(4,2),-- Tolleranza al freddo (es: -5.50)
    
    -- Usi e Avvertenze
    is_toxic BOOLEAN DEFAULT FALSE, -- 0 = No, 1 = Sì
    primary_uses TEXT,              -- Es: Ornamentale, Culinario, Officinale
    
    -- Metadati
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);