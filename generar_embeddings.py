import os
import json
import psycopg2
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT"),
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
ARCHIVO_EMBEDDINGS = "embeddings.json"


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def main():
    print(f"Cargando modelo {MODELO_EMBEDDINGS}...")
    modelo = SentenceTransformer(MODELO_EMBEDDINGS)

    print("Conectando a la base de datos...")
    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, nro_boletin, descripcion
            FROM public.boletines
            WHERE descripcion IS NOT NULL
              AND descripcion != ''
            ORDER BY id
        """)
        boletines = cur.fetchall()

    conn.close()

    # Cargar embeddings existentes si ya hay un archivo previo
    if os.path.exists(ARCHIVO_EMBEDDINGS):
        with open(ARCHIVO_EMBEDDINGS, "r", encoding="utf-8") as f:
            embeddings_existentes = json.load(f)
        ids_existentes = {str(e["id"]) for e in embeddings_existentes}
        print(f"Embeddings existentes: {len(embeddings_existentes)}")
    else:
        embeddings_existentes = []
        ids_existentes = set()

    boletines_nuevos = [b for b in boletines if str(b[0]) not in ids_existentes]
    print(f"Boletines nuevos a procesar: {len(boletines_nuevos)}")

    if not boletines_nuevos:
        print("Todo está al día, no hay nada nuevo que procesar.")
        return

    for boletin in tqdm(boletines_nuevos, desc="Generando embeddings"):
        id_bd, nro_boletin, descripcion = boletin
        try:
            vector = modelo.encode(descripcion).tolist()
            embeddings_existentes.append({
                "id": id_bd,
                "nro_boletin": nro_boletin,
                "vector": vector
            })
        except Exception as e:
            print(f"\nError en boletín {nro_boletin}: {e}")

    with open(ARCHIVO_EMBEDDINGS, "w", encoding="utf-8") as f:
        json.dump(embeddings_existentes, f)

    print(f"\nEmbeddings guardados en {ARCHIVO_EMBEDDINGS}")
    print(f"Total: {len(embeddings_existentes)}")


if __name__ == "__main__":
    main()