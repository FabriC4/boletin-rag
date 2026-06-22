import os
import re
import pdfplumber
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
# LECTURA DE PDF DESDE DISCO
# ========================
def obtener_contenido_pdf(nro_boletin: int) -> str:
    """Lee el PDF desde la carpeta local boletines/"""
    conn = get_connection()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT patharchivo FROM boletines WHERE nro_boletin = %s",
            (nro_boletin,)
        )
        row = cur.fetchone()

    conn.close()

    if not row or not row[0]:
        return None

    path_archivo = row[0].strip()
    nombre_archivo = os.path.basename(path_archivo)
    ruta_local = os.path.join("boletines", nombre_archivo)

    if not os.path.exists(ruta_local):
        print(f"PDF no encontrado en disco: {ruta_local}")
        return None

    try:
        texto = ""
        with pdfplumber.open(ruta_local) as pdf:
            for page in pdf.pages:
                texto += page.extract_text() or ""
        return texto.strip() if texto.strip() else None
    except Exception as e:
        print(f"Error leyendo PDF {ruta_local}: {e}")
        return None


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
    numeros_pregunta = [int(n) for n in re.findall(r'\b\d{4,}\b', pregunta)]
    contexto = ""

    for b in boletines:
        id_bd, nro, nro2, desc, fecha, estado, tipo, sim = b

        contexto += f"""
--- Boletín #{nro} ---
Tipo: {tipo or 'Sin tipo'}
Fecha: {fecha or 'Sin fecha'}
Estado: {'Activo' if estado else 'Inactivo'}
Descripción: {desc}
"""
        try:
            nro_int = int(nro)
        except (ValueError, TypeError):
            nro_int = None

        if nro_int in numeros_pregunta or len(boletines) == 1:
            print(f"Trayendo texto del PDF para el boletín {nro_int}...")
            contenido = obtener_contenido_pdf(nro_int)

            if contenido:
                contexto += f"Contenido completo del PDF:\n{contenido[:4000]}\n"
            else:
                contexto += "Contenido del PDF: No disponible en disco.\n"

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