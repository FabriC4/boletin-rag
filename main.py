import os
import re
import httpx
import pdfplumber
import io
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT"),
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
PDF_BASE_URL      = "https://www.boletindigital.misiones.gov.ar/boletines/{}.pdf"

app = FastAPI(title="Consultor de Boletines")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/logo.png")
def logo():
    return FileResponse("logo.png")

@app.get("/")
def index():
    return FileResponse("index.html")

print("Cargando modelo de embeddings...")
modelo = SentenceTransformer(MODELO_EMBEDDINGS)
print("Modelo cargado.")

groq_client = Groq(api_key=GROQ_API_KEY)


class Consulta(BaseModel):
    pregunta: str
    top_k: int = 5


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


# ========================
# DESCARGA Y EXTRACCION PDF
# ========================
def descargar_pdf(nro_boletin: int) -> str:
    """Descarga el PDF del boletín probando diferentes formatos de URL si falla,

    incluyendo errores típicos de carga (como ceros de más al final).
    """

    # Generamos la lista de URLs posibles para este número de boletín específico
    formatos_url = [
        f"https://www.boletindigital.misiones.gov.ar/boletines/{nro_boletin}.pdf",  # Formato limpio (Ej: 14630.pdf)
        f"https://www.boletindigital.misiones.gov.ar/boletines/bo{nro_boletin}.pdf",  # Formato viejo (Ej: bo12256.pdf)
        f"https://www.boletindigital.misiones.gov.ar/boletines/{nro_boletin}0.pdf",  # ERROR DE CARGA: Cero extra al final (Ej: 146300.pdf)
    ]

    for url in formatos_url:
        print(f"Probando descarga desde URL: {url}")

        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)

            # Si el servidor responde con 200 OK, encontramos el archivo correcto
            if response.status_code == 200:
                texto = ""
                with pdfplumber.open(io.BytesIO(response.content)) as pdf:
                    for page in pdf.pages:
                        texto += page.extract_text() or ""

                if texto.strip():
                    print(f"¡Éxito! Conectado y procesado desde: {url}")
                    return texto.strip()

            print(
                f"No encontrado (Status {response.status_code}) en la ruta: {url}"
            )

        except Exception as e:
            print(f"Error de red o procesamiento en {url}: {e}")
            continue

    print(
        f"Imposible descargar el boletín {nro_boletin}. Ningún patrón de URL coincidió en el servidor."
    )
    return None


def obtener_contenido_pdf(nro_boletin: int) -> str:
    """Busca el contenido del PDF en la BD, si no lo tiene lo descarga y guarda."""
    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT contenido_pdf FROM boletines WHERE nro_boletin = %s",
            (nro_boletin,)
        )
        row = cur.fetchone()

    if row and row[0]:
        conn.close()
        return row[0]

    print(f"Descargando PDF del boletín {nro_boletin}...")
    contenido = descargar_pdf(nro_boletin)

    if contenido:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE boletines SET contenido_pdf = %s WHERE nro_boletin = %s",
                (contenido, nro_boletin)
            )
        conn.commit()
        print(f"PDF del boletín {nro_boletin} guardado en BD.")

    conn.close()
    return contenido


# ========================
# BUSQUEDA DE BOLETINES
# ========================
def buscar_boletines(pregunta: str, top_k: int):
    numeros = re.findall(r'\b\d{4,}\b', pregunta)
    conn = get_connection()

    if numeros:
        placeholders = ','.join(['%s'] * len(numeros))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    b.id,
                    b.nro_boletin,
                    b.nro_boletin2,
                    b.descripcion,
                    b.fecha,
                    b.estado,
                    tb.descripcion AS tipo_boletin,
                    1.0 AS similitud
                FROM public.boletines b
                LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                WHERE b.nro_boletin IN ({placeholders})
            """, numeros)
            resultados = cur.fetchall()
        conn.close()
        if resultados:
            return resultados
        conn = get_connection()

    embedding = modelo.encode(pregunta).tolist()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                b.id,
                b.nro_boletin,
                b.nro_boletin2,
                b.descripcion,
                b.fecha,
                b.estado,
                tb.descripcion AS tipo_boletin,
                1 - (b.embedding <=> %s::vector) AS similitud
            FROM public.boletines b
            LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
            WHERE b.embedding IS NOT NULL
            ORDER BY b.embedding <=> %s::vector
            LIMIT %s
        """, (embedding, embedding, top_k))
        resultados = cur.fetchall()
    conn.close()
    return resultados


# ========================
# CONSTRUCCION DEL CONTEXTO
# ========================
def construir_contexto(boletines, pregunta: str):
    # Convertimos todos los números encontrados en la pregunta a enteros para comparar limpiamente
    numeros_pregunta = [int(n) for n in re.findall(r'\b\d{4,}\b', pregunta)]
    contexto = ""

    for b in boletines:
        # Desempaquetamos de forma segura
        id_bd, nro, nro2, desc, fecha, estado, tipo, sim = b

        contexto += f"""
--- Boletín #{nro} ---
Tipo: {tipo or 'Sin tipo'}
Fecha: {fecha or 'Sin fecha'}
Estado: {'Activo' if estado else 'Inactivo'}
Descripción: {desc}
"""
        # Forzamos la conversión a int de 'nro' por si las moscas viene como string o float de la BD
        try:
            nro_int = int(nro)
        except (ValueError, TypeError):
            nro_int = None

        # Si el número de este boletín coincide con el que pidió el usuario OR si la búsqueda 
        # fue tan específica que trajimos pocos boletines, le metemos el contenido del PDF.
        if nro_int in numeros_pregunta or len(boletines) == 1:
            print(f" Trayendo texto del PDF para el boletín {nro_int}...")
            contenido = obtener_contenido_pdf(nro_int)
            
            if contenido:
                # Le pasamos los primeros 4000 caracteres para no saturar el contexto de Groq
                contexto += f"Contenido completo del PDF:\n{contenido[:4000]}\n"
            else:
                contexto += "Contenido del PDF: No disponible en el servidor o no se pudo descargar.\n"

    return contexto


# ========================
# CONSULTA A GROQ
# ========================
def consultar_groq(pregunta: str, contexto: str):
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """Sos un asistente experto en boletines oficiales.
Respondé la pregunta ÚNICAMENTE basándote en los boletines proporcionados.
Si la información no está en los boletines, decilo claramente.
Respondé siempre en español."""
                },
                {
                    "role": "user",
                    "content": f"""BOLETINES RELEVANTES:
{contexto}

PREGUNTA: {pregunta}"""
                }
            ],
            max_tokens=1024,
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar Groq: {str(e)}")


# ========================
# ENDPOINTS
# ========================
@app.post("/consulta")
def consulta(body: Consulta):
    if not body.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía")

    boletines = buscar_boletines(body.pregunta, body.top_k)

    if not boletines:
        raise HTTPException(status_code=404, detail="No se encontraron boletines relevantes")

    contexto = construir_contexto(boletines, body.pregunta)
    respuesta = consultar_groq(body.pregunta, contexto)

    return {
        "respuesta": respuesta,
        "boletines_usados": [
            {
                "id": b[0],
                "nro_boletin": b[1],
                "tipo": b[6],
                "fecha": str(b[4]) if b[4] else None,
                "similitud": round(b[7], 4)
            }
            for b in boletines
        ]
    }


@app.get("/health")
def health():
    return {"status": "ok", "modelo": GROQ_MODEL}