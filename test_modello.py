import os
import sys

try:
    import tf_keras as keras
except ImportError:
    print("\n" + "!"*60)
    print("ERRORE: Per caricare questo modello serve il pacchetto 'tf-keras'.")
    print("Nel tuo terminale esegui questo comando, poi riavvia lo script:")
    print("pip install tf-keras")
    print("!"*60 + "\n")
    sys.exit(1)

import numpy as np
import tensorflow as tf
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report
import matplotlib.pyplot as plt
import seaborn as sns

def load_labels(labels_path):
    with open(labels_path, 'r') as f:
        # I file esportati da Teachable Machine hanno il formato "0 NomeClasse"
        labels = [line.strip().split(' ', 1)[1] if ' ' in line else line.strip() for line in f.readlines() if line.strip()]
    return labels

def main():
    model_path = 'models/keras_model.h5'
    labels_path = 'models/labels.txt'
    test_dir = 'Dataset/Test_set'

    # Verifica l'esistenza dei file e delle cartelle necessari
    if not os.path.exists(model_path):
        print(f"Errore: Modello non trovato in {model_path}")
        return
    if not os.path.exists(test_dir):
        print(f"Errore: Cartella di test non trovata in {test_dir}")
        return

    # Inizializza le labels originali
    original_labels = None
    if os.path.exists(labels_path):
        original_labels = load_labels(labels_path)
        print(f"Classi attese (da labels.txt): {original_labels}")
    else:
        print(f"Attenzione: file labels {labels_path} non trovato. Verrà usato l'ordine alfabetico di default.")

    # Carica il modello Keras usando tf_keras (Keras 2 nativo) per evitare i bug di compatibilità di Keras 3
    print("\nCaricamento del modello...")
    model = keras.models.load_model(model_path, compile=False) 

    # Dimensione di input tipica per modelli Teachable Machine
    input_shape = (224, 224)
    
    print("\nPreparazione dei dati di test...")
    # I modelli Teachable Machine richiedono la normalizzazione nell'intervallo [-1, 1]
    def preprocess_tm(img):
        return (img / 127.5) - 1.0

    from tf_keras.preprocessing.image import ImageDataGenerator
    test_datagen = ImageDataGenerator(preprocessing_function=preprocess_tm)
    
    # Caricamento immagini di test
    # CORREZIONE: Forziamo il parametro 'classes' a seguire l'ordine di labels.txt
    test_generator = test_datagen.flow_from_directory(
        test_dir,
        target_size=input_shape,
        batch_size=32,
        class_mode='categorical',
        shuffle=False, # IMPERATIVO: non mescolare i dati per mantenere la corrispondenza con le labels vere
        classes=original_labels 
    )

    if test_generator.samples == 0:
        print("Nessuna immagine trovata nel Test_set. Verifica che i nomi delle cartelle corrispondano esattamente alle labels.")
        return

    print("\nCalcolo delle predizioni sui dati di test...")
    predictions = model.predict(test_generator)
    
    # Prende la classe con la probabilità maggiore
    y_pred = np.argmax(predictions, axis=1)
    y_true = test_generator.classes

    # Mappa le classi estratte dal generatore (che ora seguono l'ordine corretto)
    class_indices = test_generator.class_indices
    index_to_class = {v: k for k, v in class_indices.items()}
    generator_labels = [index_to_class[i] for i in range(len(index_to_class))]

    # Calcolo Metriche tramite scikit-learn
    accuracy = accuracy_score(y_true, y_pred)
    cm = confusion_matrix(y_true, y_pred)
    
    try:
        report = classification_report(y_true, y_pred, target_names=generator_labels)
    except Exception as e:
        report = "Impossibile generare il report di classificazione: " + str(e)

    print("\n" + "="*50)
    print("RISULTATI DELLA VALUTAZIONE")
    print("="*50)
    print(f"\nAccuracy del modello: {accuracy * 100:.2f}%\n")
    print("Classification Report:")
    print(report)

    # Generazione e salvataggio della Confusion Matrix
    print("\nGenerazione del grafico della Confusion Matrix...")
    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', 
                xticklabels=generator_labels, yticklabels=generator_labels)
    plt.title('Confusion Matrix')
    plt.ylabel('Classe Reale')
    plt.xlabel('Classe Predetta')
    plt.tight_layout()
    
    cm_filename = 'confusion_matrix.png'
    plt.savefig(cm_filename)
    print(f"Confusion Matrix salvata con successo come '{cm_filename}' nella cartella corrente.")
    
    # Mostra la Confusion Matrix a schermo
    plt.show()

if __name__ == "__main__":
    main()