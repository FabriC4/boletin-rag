import os
import httpx
import psycopg2
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

CARPETA_BOLETINES = "boletines"


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def construir_url(path_archivo: str) -> str:
    path_limpio = path_archivo.strip().lstrip('/')
    if path_limpio.startswith("boletines/"):
        return f"https://www.boletindigital.misiones.gov.ar/{path_limpio}"
    return f"https://www.boletindigital.misiones.gov.ar/boletines/{path_limpio}"


def main():
    os.makedirs(CARPETA_BOLETINES, exist_ok=True)

    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, nro_boletin, patharchivo
            FROM boletines
            WHERE patharchivo IS NOT NULL
              AND patharchivo != ''
            ORDER BY nro_boletin
        """)
        boletines = cur.fetchall()

    conn.close()

    total = len(boletines)
    print(f"Boletines a descargar: {total}")
    print("-" * 50)

    exitosos = 0
    omitidos = 0
    fallidos = 0

    for boletin in tqdm(boletines, desc="Descargando PDFs"):
        id_bd, nro_boletin, path_archivo = boletin

        # Nombre del archivo local
        nombre_archivo = os.path.basename(path_archivo.strip())
        ruta_local = os.path.join(CARPETA_BOLETINES, nombre_archivo)

        # Si ya existe en disco lo saltamos
        if os.path.exists(ruta_local):
            omitidos += 1
            continue

        url = construir_url(path_archivo)

        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)

            if response.status_code != 200:
                fallidos += 1
                continue

            # Guardar el PDF en disco
            with open(ruta_local, 'wb') as f:
                f.write(response.content)

            exitosos += 1

        except Exception as e:
            fallidos += 1
            print(f"\nError en boletín {nro_boletin}: {e}")

    print("\n" + "=" * 50)
    print(f"Descargados: {exitosos}")
    print(f"Omitidos (ya existían): {omitidos}")
    print(f"Fallidos: {fallidos}")
    print(f"Total:    {total}")


if __name__ == "__main__":
    main()