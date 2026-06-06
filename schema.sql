-- Creazione del Database (se non esistente)
CREATE DATABASE IF NOT EXISTS smart_agri;
USE smart_agri;

-- Tabella Plants
CREATE TABLE IF NOT EXISTS plants (
    plant_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
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
