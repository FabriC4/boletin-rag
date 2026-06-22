import os
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

MODELO = "paraphrase-multilingual-MiniLM-L12-v2"

def main():
    print(f"Cargando modelo {MODELO}...")
    modelo = SentenceTransformer(MODELO)

    print("Conectando a la base de datos...")
    conn = psycopg2.connect(**DB_CONFIG)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, nro_boletin, descripcion
            FROM public.boletines
            WHERE descripcion IS NOT NULL
              AND descripcion != ''
              AND embedding IS NULL
            ORDER BY id
        """)
        boletines = cur.fetchall()

    total = len(boletines)
    print(f"Boletines a procesar: {total}")
    print("-" * 50)

    errores = []

    for boletin in tqdm(boletines, desc="Generando embeddings"):
        boletin_id, nro_boletin, descripcion = boletin
        try:
            embedding = modelo.encode(descripcion).tolist()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.boletines SET embedding = %s WHERE id = %s",
                    (embedding, boletin_id)
                )
            conn.commit()
        except Exception as e:
            errores.append((boletin_id, str(e)))
            print(f"\nError en boletín {nro_boletin}: {e}")

    conn.close()

    print("\n" + "=" * 50)
    print(f"Procesados: {total - len(errores)}/{total}")
    if errores:
        print(f"Errores: {len(errores)}")
    else:
        print("Sin errores.")

if __name__ == "__main__":
    main()