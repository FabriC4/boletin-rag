import os
import json
import psycopg2
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from dotenv import load_dotenv
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import traceback
load_dotenv()

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
os.environ["PATH"] += os.pathsep + r"C:\poppler\poppler-26.02.0\Library\bin"

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT"),
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

CARPETA_BOLETINES = "boletines"
ARCHIVO_TEXTOS    = "textos_boletines.json"
WORKERS           = max(1, multiprocessing.cpu_count() - 1)

def extraer_texto_pdf(args):
    """Función que corre en cada proceso worker."""
    nro_boletin, path_archivo = args

    # Cada worker necesita configurar sus propias rutas
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    os.environ["PATH"] += os.pathsep + r"C:\poppler\poppler-26.02.0\Library\bin"

    nombre_archivo = os.path.basename(path_archivo.strip())
    ruta_local = os.path.join(CARPETA_BOLETINES, nombre_archivo)

    # 1. DETECTAR SI EL ARCHIVO NO EXISTE FÍSICAMENTE
    if not os.path.exists(ruta_local):
        with open("errores_extraccion.txt", "a", encoding="utf-8") as log_file:
            log_file.write(f"=== ARCHIVO FALTANTE BOLETÍN #{nro_boletin} ===\n")
            log_file.write(f"Se buscó el archivo en: {ruta_local}\n")
            log_file.write("Resultado: El archivo no existe en la carpeta 'boletines'.\n")
            log_file.write("-" * 50 + "\n\n")
        return None

    texto_final = ""

    try:
        # Intento 1: texto nativo con pdfplumber (rápido)
        with pdfplumber.open(ruta_local) as pdf:
            for page in pdf.pages:
                texto_final += page.extract_text() or ""

        # Intento 2: si el texto es muy corto activa OCR (lento pero necesario)
        if len(texto_final.strip()) < 150:
            texto_final = ""
            # Nota: le pasamos la ruta de poppler explícita para evitar fallas en subprocesos
            paginas_imagenes = convert_from_path(ruta_local, dpi=150, poppler_path=r"C:\poppler\poppler-26.02.0\Library\bin")

            with pdfplumber.open(ruta_local) as pdf:
                for i, (page, imagen) in enumerate(zip(pdf.pages, paginas_imagenes)):
                    texto_nativo = page.extract_text() or ""
                    texto_ocr = pytesseract.image_to_string(imagen, lang='spa')

                    texto_pagina = texto_nativo.strip() + "\n"
                    for linea in texto_ocr.splitlines():
                        linea = linea.strip()
                        if linea and linea not in texto_nativo:
                            texto_pagina += linea + "\n"

                    texto_final += f"--- PÁGINA {i+1} ---\n{texto_pagina}\n"

    except Exception as e:
        # 2. CAPTURAR CRASHES EN EL TXT
        error_detalle = traceback.format_exc()
        with open("errores_extraccion.txt", "a", encoding="utf-8") as log_file:
            log_file.write(f"=== CRASH BOLETÍN #{nro_boletin} ===\n{error_detalle}\n\n")
        return None

    # 3. DETECTAR SI EL PDF QUEDÓ COMPLEMENTE VACÍO
    if not texto_final.strip():
        peso_archivo = os.path.getsize(ruta_local)
        with open("errores_extraccion.txt", "a", encoding="utf-8") as log_file:
            log_file.write(f"=== RECHAZADO BOLETÍN #{nro_boletin} ===\n")
            log_file.write(f"Ruta: {ruta_local}\n")
            log_file.write(f"Peso: {peso_archivo} bytes\n")
            log_file.write("Motivo: El proceso terminó pero no se pudo extraer ninguna letra (PDF vacío o ilegible).\n")
            log_file.write("-" * 50 + "\n\n")
        return None

    return {
        "nro_boletin": nro_boletin,
        "patharchivo": path_archivo.strip(),
        "texto": texto_final.strip()
    }


def get_connection():
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main():
    # Cargar textos ya procesados
    if os.path.exists(ARCHIVO_TEXTOS):
        with open(ARCHIVO_TEXTOS, "r", encoding="utf-8") as f:
            textos = json.load(f)
        procesados = {str(t["nro_boletin"]) for t in textos}
        print(f"Textos ya procesados: {len(textos)}")
    else:
        textos = []
        procesados = set()

    # Traer boletines de la BD
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT nro_boletin, patharchivo
            FROM boletines
            WHERE patharchivo IS NOT NULL AND patharchivo != ''
            ORDER BY nro_boletin
        """)
        boletines = cur.fetchall()
    conn.close()

    pendientes = [(str(nro), path) for nro, path in boletines if str(nro) not in procesados]
    print(f"Boletines pendientes: {len(pendientes)}")
    print(f"Workers paralelos: {WORKERS}")
    print("-" * 50)

    exitosos = 0
    fallidos = 0
    GUARDADO_CADA = 100

    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(extraer_texto_pdf, args): args for args in pendientes}

        with tqdm(total=len(pendientes), desc="Extrayendo textos") as pbar:
            for future in as_completed(futures):
                resultado = future.result()

                if resultado:
                    textos.append(resultado)
                    exitosos += 1
                else:
                    fallidos += 1

                pbar.update(1)

                # Guardar cada N boletines para no perder progreso
                if (exitosos + fallidos) % GUARDADO_CADA == 0:
                    with open(ARCHIVO_TEXTOS, "w", encoding="utf-8") as f:
                        json.dump(textos, f, ensure_ascii=False)

    # Guardado final
    with open(ARCHIVO_TEXTOS, "w", encoding="utf-8") as f:
        json.dump(textos, f, ensure_ascii=False)

    print(f"\nExitosos: {exitosos}")
    print(f"Fallidos: {fallidos}")
    print(f"Total en JSON: {len(textos)}")
    print(f"Guardado en: {ARCHIVO_TEXTOS}")


if __name__ == "__main__":
    main()