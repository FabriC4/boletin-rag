import os
import re
import json
import numpy as np
import pdfplumber
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from groq import Groq
from pdf2image import convert_from_path
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
os.environ["PATH"] += os.pathsep + r"C:\poppler\poppler-26.02.0\Library\bin"

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT"),
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

GROQ_API_KEY      = os.getenv("GROQ_API_KEY")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
ARCHIVO_EMBEDDINGS = "embeddings.json"
CARPETA_BOLETINES  = "boletines"

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

print("Cargando embeddings desde JSON...")
with open(ARCHIVO_EMBEDDINGS, "r", encoding="utf-8") as f:
    datos_embeddings = json.load(f)

vectores_matrix = np.array([e["vector"] for e in datos_embeddings])
print(f"Embeddings cargados: {len(datos_embeddings)}")

groq_client = Groq(api_key=GROQ_API_KEY)


class Mensaje(BaseModel):
    role: str
    content: str

class Consulta(BaseModel):
    pregunta: str
    historial: Optional[List[Mensaje]] = []
    top_k: int = 5


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


# ========================
# BUSQUEDA SEMANTICA EN JSON
# ========================
def buscar_por_similitud(pregunta: str, top_k: int):
    vector_pregunta = modelo.encode(pregunta)

    # Similitud coseno
    norma_pregunta = np.linalg.norm(vector_pregunta)
    normas_matrix  = np.linalg.norm(vectores_matrix, axis=1)
    similitudes    = np.dot(vectores_matrix, vector_pregunta) / (normas_matrix * norma_pregunta + 1e-10)

    indices_top = np.argsort(similitudes)[::-1][:top_k]

    return [
        {
            "id": datos_embeddings[i]["id"],
            "nro_boletin": datos_embeddings[i]["nro_boletin"],
            "similitud": float(similitudes[i])
        }
        for i in indices_top
    ]


# ========================
# BUSQUEDA DE BOLETINES
# ========================
def buscar_boletines(pregunta: str, top_k: int):
    numeros = re.findall(r'\b\d{4,}\b', pregunta)
    conn = get_connection()

    # Si la pregunta tiene números buscamos por número exacto
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
        if resultados:
            conn.close()
            return resultados

    # Búsqueda semántica en JSON
    similares = buscar_por_similitud(pregunta, top_k)
    ids = [s["id"] for s in similares]
    similitud_map = {s["id"]: s["similitud"] for s in similares}

    placeholders = ','.join(['%s'] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                b.id,
                b.nro_boletin,
                b.nro_boletin2,
                b.descripcion,
                b.fecha,
                b.estado,
                tb.descripcion AS tipo_boletin
            FROM public.boletines b
            LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
            WHERE b.id IN ({placeholders})
        """, ids)
        rows = cur.fetchall()

    conn.close()

    # Agregar similitud y ordenar
    resultados = [row + (similitud_map[row[0]],) for row in rows]
    resultados.sort(key=lambda x: x[-1], reverse=True)
    return resultados


# ========================
# LECTURA DE PDF DESDE DISCO
# ========================
def obtener_contenido_pdf(nro_boletin: int) -> str:
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

    nombre_archivo = os.path.basename(row[0].strip())
    ruta_local = os.path.join(CARPETA_BOLETINES, nombre_archivo)

    if not os.path.exists(ruta_local):
        print(f"PDF no encontrado en disco: {ruta_local}")
        return None

    texto_final = ""

    try:
        # Convertir todas las páginas del PDF en imágenes
        paginas_imagenes = convert_from_path(ruta_local, dpi=150)

        with pdfplumber.open(ruta_local) as pdf:
            for i, (page, imagen) in enumerate(zip(pdf.pages, paginas_imagenes)):

                texto_pagina = ""

                # Paso 1: extraer texto nativo de la página
                texto_nativo = page.extract_text() or ""

                # Paso 2: extraer texto via OCR de la imagen de esa página
                texto_ocr = pytesseract.image_to_string(imagen, lang='spa')

                # Combinar ambos textos eliminando duplicados simples
                if texto_nativo.strip():
                    texto_pagina += texto_nativo.strip() + "\n"

                # Agregar líneas del OCR que no estén ya en el texto nativo
                for linea in texto_ocr.splitlines():
                    linea = linea.strip()
                    if linea and linea not in texto_nativo:
                        texto_pagina += linea + "\n"

                texto_final += f"--- PÁGINA {i+1} ---\n{texto_pagina}\n"

    except Exception as e:
        print(f"Error procesando PDF {ruta_local}: {e}")
        return None

    return texto_final.strip() if texto_final.strip() else None


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

        if nro_int in numeros_pregunta or len(boletines) <= 3:
            print(f"Trayendo PDF para el boletín {nro_int}...")
            contenido = obtener_contenido_pdf(nro_int)
            if contenido:
                contexto += f"Contenido completo del PDF:\n{contenido[:8000]}\n"
            else:
                contexto += "Contenido del PDF: No disponible en disco.\n"

    return contexto


# ========================
# CONSULTA A GROQ CON MEMORIA
# ========================
def consultar_groq(pregunta: str, contexto: str, historial: list):
    messages = [
        {
            "role": "system",
            "content": """Sos un sistema experto en análisis de boletines oficiales de la provincia de Misiones.
Tu objetivo es dar respuestas completas, exhaustivas y detalladas basándote en los boletines proporcionados.

REGLAS:
1. Respondé ÚNICAMENTE basándote en los boletines del contexto.
2. Si encontrás nombres, empresas, expedientes o montos, listalos todos sin omitir ninguno.
3. Usá tablas de Markdown cuando haya datos estructurados.
4. Usá negrita para nombres propios, empresas y números de expedientes.
5. Si no encontrás información, decilo claramente.
6. Respondé siempre en español."""
        }
    ]

    # Agregar historial de conversación
    for msg in historial:
        messages.append({"role": msg.role, "content": msg.content})

    # Agregar la pregunta actual con el contexto
    messages.append({
        "role": "user",
        "content": f"""BOLETINES RELEVANTES:
{contexto}

PREGUNTA: {pregunta}"""
    })

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=4096,
            temperature=0.1
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
    respuesta = consultar_groq(body.pregunta, contexto, body.historial or [])

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