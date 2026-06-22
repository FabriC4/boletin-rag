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
from pdf2image import convert_from_path
import pytesseract

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
def obtener_contenido_pdf(nro_boletin: int):
    # Asumo que tus PDFs están guardados con el número en una carpeta
    pdf_path = f"boletines/{nro_boletin}.pdf" 
    
    if not os.path.exists(pdf_path):
        return None

    texto_extraido = ""
    
    # PASO 1: Intentar extracción nativa rápida con pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        for pagina in pdf.pages:
            texto_pag = pagina.extract_text()
            if texto_pag:
                texto_extraido += texto_pag + "\n"
    
    # PASO 2: CONTROL DE FLEXIBILIDAD (¿Es un PDF escaneado o una imagen?)
    # Si el texto extraído es casi nulo (menos de 150 caracteres en todo el PDF),
    # significa que el PDF es una imagen o un escaneo sin texto real.
    if len(texto_extraido.strip()) < 150:
        print(f"⚠️ Boletín {nro_boletin} detectado como IMAGEN/ESCANEO. Activando OCR...")
        texto_extraido = "" # Limpiamos por las dudas
        
        try:
            # Convertimos las páginas del PDF en imágenes en memoria
            paginas_como_imagenes = convert_from_path(pdf_path, dpi=150)
            
            # Pasamos el OCR de Tesseract página por página (configurado en español)
            for i, imagen in enumerate(paginas_como_imagenes):
                texto_ocr = pytesseract.image_to_string(imagen, lang='spa')
                texto_extraido += f"--- PÁGINA {i+1} (Extraída por OCR) ---\n{texto_ocr}\n"
                
            print(f"✅ OCR finalizado con éxito para el boletín {nro_boletin}.")
        except Exception as e:
            print(f"❌ Error en el proceso de OCR: {str(e)}")
            return None

    return texto_extraido

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

        # Si el usuario mencionó el número, o si la búsqueda semántica trajo este boletín como relevante
        if nro_int in numeros_pregunta or len(boletines) <= 3: 
            print(f"Trayendo texto COMPLETO del PDF para el boletín {nro_int}...")
            contenido = obtener_contenido_pdf(nro_int)

            if contenido:
                # Quitamos el límite de 4000 y le pasamos todo el documento (hasta 200k caracteres por seguridad)
                contexto += f"Contenido completo del PDF:\n{contenido[:200000]}\n"
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
                    "content": """Sos un sistema experto e imbatible en análisis de boletines oficiales de la provincia.
Tu objetivo es dar respuestas extremadamente completas, exhaustivas y detalladas. No resumas de más ni omitas datos.

REGLAS DE FUNCIONAMIENTO:
1. Respondé ÚNICAMENTE basándote en los boletines proporcionados en el contexto.
2. Si la pregunta es sobre buscar nombres, empresas, deudas o edictos, revisá TODO el texto del PDF adjunto en el contexto.
3. Transcribí o volcá TODOS los resultados que encuentres. Si hay 20 personas afectadas en un cuadro de Rentas o Sucesores, listá las 20 personas. No uses "etc." ni recortes las listas.
4. FORMATO: Usá títulos (##, ###) para separar temas. Si encontrás datos estructurados (Nombres, Expedientes, Montos, Fechas), organizalos OBLIGATORIAMENTE en Tablas de Markdown limpias.
5. Usá negrita para nombres propios, empresas (S.R.L., S.A.) y números de expedientes."""
                },
                {
                    "role": "user",
                    "content": f"""BOLETINES RELEVANTES PARA ANALIZAR:
{contexto}

PREGUNTA DEL USUARIO: {pregunta}"""
                }
            ],
            max_tokens=4096, # Le damos el máximo permitido de salida para que no se corte a mitad de una tabla
            temperature=0.1  # Bajamos la temperatura a 0.1 para que sea ultra preciso con los datos y no invente nada
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