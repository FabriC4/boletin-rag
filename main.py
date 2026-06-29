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
from collections import Counter
from datetime import datetime
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

GROQ_API_KEY         = os.getenv("GROQ_API_KEY")
GROQ_MODEL           = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODELO_EMBEDDINGS    = "paraphrase-multilingual-MiniLM-L12-v2"
ARCHIVO_EMBEDDINGS   = "embeddings.json"
ARCHIVO_TEXTOS       = "textos_boletines.json"
CARPETA_BOLETINES    = "boletines"
ARCHIVO_ESTADISTICAS = "estadisticas.json"
ADMIN_USER           = "admin"
ADMIN_PASS           = "ccpm2026"
PDF_BASE_URL         = "https://www.boletindigital.misiones.gov.ar/boletines/"

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

@app.get("/admin")
def admin():
    return FileResponse("admin.html")

print("Cargando modelo de embeddings...")
modelo = SentenceTransformer(MODELO_EMBEDDINGS)
print("Modelo cargado.")

print("Cargando embeddings desde JSON...")
with open(ARCHIVO_EMBEDDINGS, "r", encoding="utf-8") as f:
    datos_embeddings = json.load(f)

vectores_matrix = np.array([e["vector"] for e in datos_embeddings])
print(f"Embeddings cargados: {len(datos_embeddings)}")

print("Cargando textos completos extraídos desde JSON...")
if os.path.exists(ARCHIVO_TEXTOS):
    with open(ARCHIVO_TEXTOS, "r", encoding="utf-8") as f:
        lista_textos = json.load(f)
    mapa_textos_completos = {str(t["nro_boletin"]): t["texto"] for t in lista_textos}
    print(f"Textos de boletines precargados: {len(mapa_textos_completos)}")
else:
    mapa_textos_completos = {}
    print("⚠️ textos_boletines.json no encontrado.")

groq_client = Groq(api_key=GROQ_API_KEY)


# ========================
# HELPER: construir URL del PDF
# ========================
def construir_url_pdf(patharchivo: str) -> str:
    if not patharchivo:
        return None
    path_limpio = patharchivo.strip().lstrip('/')
    if path_limpio.startswith("boletines/"):
        return f"https://www.boletindigital.misiones.gov.ar/{path_limpio}"
    return f"{PDF_BASE_URL}{path_limpio}"


# ========================
# ESTADISTICAS
# ========================
def cargar_estadisticas():
    if os.path.exists(ARCHIVO_ESTADISTICAS):
        with open(ARCHIVO_ESTADISTICAS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"consultas": []}

def guardar_estadisticas(stats):
    with open(ARCHIVO_ESTADISTICAS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)


# ========================
# MODELOS
# ========================
class Mensaje(BaseModel):
    role: str
    content: str

class Consulta(BaseModel):
    pregunta: str
    historial: Optional[List[Mensaje]] = []
    top_k: int = 10

class LoginAdmin(BaseModel):
    usuario: str
    password: str


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def obtener_fragmento_contexto(texto_completo, palabras_buscadas, ventana=300):
    """
    Busca la posición donde se concentran las palabras clave del usuario
    y recorta un fragmento limpio alrededor de ellas.
    """
    texto_upper = texto_completo.upper()
    palabras_upper = [p.upper() for p in palabras_buscadas if len(p) > 3]
    
    if not palabras_upper:
        return texto_completo[:ventana] + "..."

    # Buscamos dónde aparece la primera palabra clave fuerte
    posicion_inicio = -1
    for palabra in palabras_upper:
        pos = texto_upper.find(palabra)
        if pos != -1:
            posicion_inicio = pos
            break

    if posicion_inicio == -1:
        return texto_completo[:ventana] + "..."

    # Coordinamos los márgenes de recorte de la ventana de texto
    inicio = max(0, posicion_inicio - 80)
    fin = min(len(texto_completo), inicio + ventana)
    
    fragmento = texto_completo[inicio:fin]
    
    if inicio > 0:
        fragmento = "..." + fragmento
    if fin < len(texto_completo):
        fragmento = fragmento + "..."
        
    return fragmento

# ========================
# BUSQUEDA SEMANTICA EN JSON
# ========================
def buscar_por_similitud(pregunta: str, top_k: int):
    vector_pregunta = modelo.encode(pregunta)
    norma_pregunta  = np.linalg.norm(vector_pregunta)
    normas_matrix   = np.linalg.norm(vectores_matrix, axis=1)
    similitudes     = np.dot(vectores_matrix, vector_pregunta) / (normas_matrix * norma_pregunta + 1e-10)
    indices_top     = np.argsort(similitudes)[::-1][:top_k]

    return [
        {
            "nro_boletin": datos_embeddings[i]["nro_boletin"],
            "texto_chunk": datos_embeddings[i]["texto_chunk"],
            "patharchivo": datos_embeddings[i]["patharchivo"],
            "similitud": float(similitudes[i])
        }
        for i in indices_top
    ]


# ========================
# MOTOR DE BÚSQUEDA EXPLÍCITA
# ========================
def buscar_boletines(pregunta: str, top_k: int):
    numeros = re.findall(r'\b\d{4,}\b', pregunta)
    conn = get_connection()

    # 1. Búsqueda directa por número de boletín
    if numeros:
        placeholders = ','.join(['%s'] * len(numeros))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                       b.fecha, b.estado, tb.descripcion AS tipo_boletin,
                       1.0 AS similitud, 'Consulta directa por número' AS texto_chunk,
                       '' AS palabra_match, b.patharchivo
                FROM public.boletines b
                LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                WHERE b.nro_boletin IN ({placeholders})
            """, numeros)
            resultados = cur.fetchall()
        conn.close()
        if resultados:
            return resultados

    resultados_finales = {}

    # 2. Limpieza de la consulta
    palabras_ruido = ["buscar", "buscame", "encuentra", "boletin", "boletines", "que", "hablen",
                      "sobre", "el", "la", "los", "en", "donde", "aparece", "aparecen", "con", "texto", "palabra"]
    palabras_limpias = [p for p in pregunta.split() if p.lower() not in palabras_ruido]
    frase_busqueda = " ".join(palabras_limpias).upper().strip() if palabras_limpias else pregunta.upper().strip()

    # 3. Escaneo literal en textos_boletines.json (Mejorado multi-palabra insensible a mayúsculas)
    boletines_con_coincidencia = []
    if palabras_limpias:
        # Pasamos todas las palabras clave a mayúsculas para comparar limpiamente
        palabras_upper = [p.upper() for p in palabras_limpias]
        
        for nro_str, texto_completo in mapa_textos_completos.items():
            texto_completo_up = texto_completo.upper()
            
            # Verificamos si TODAS las palabras ingresadas por el usuario existen en el texto
            if all(palabra in texto_completo_up for palabra in palabras_upper):
                # Guardamos como coincidencia usando la primera palabra fuerte para el fragmento de contexto
                boletines_con_coincidencia.append((nro_str, palabras_upper[0]))
            
            if len(boletines_con_coincidencia) >= top_k * 3:
                break

    # 4. Traer metadatos de coincidencias explícitas
    if boletines_con_coincidencia:
        numeros_validos = [item[0] for item in boletines_con_coincidencia]
        placeholders = ','.join(['%s'] * len(numeros_validos))

        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                       b.fecha, b.estado, tb.descripcion AS tipo_boletin,
                       b.patharchivo
                FROM public.boletines b
                LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                WHERE b.nro_boletin IN ({placeholders})
            """, numeros_validos)
            rows_lexicas = cur.fetchall()

        # Iteramos cada fila de la base de datos para calcular su extracto dinámico
        for row in rows_lexicas:
            nro_b_str   = str(row[1])
            patharchivo = row[7]

            # 1. Llamamos a la nueva función pasando la lista de palabras limpias originales
            fragmento_contexto = obtener_fragmento_contexto(mapa_textos_completos.get(nro_b_str, ""), palabras_limpias)

            # 2. Definimos qué palabra clave se envía para el resaltador del frontend
            termino_match = palabras_limpias[0] if palabras_limpias else frase_busqueda

            # 3. Estructuramos la tupla con el fragmento inteligente e indexamos
            # id, nro, nro2, desc, fecha, estado, tipo, sim, texto_chunk, palabra_match, patharchivo
            resultados_finales[row[0]] = row[:7] + (1.0, fragmento_contexto, termino_match, patharchivo)

    # 5. Fallback semántico
    if not resultados_finales:
        print(f"Sin coincidencias explícitas para '{frase_busqueda}'. Usando fallback semántico...")
        similes = buscar_por_similitud(pregunta, top_k)

        similitud_map = {str(s["nro_boletin"]): s["similitud"] for s in similes}
        chunk_map     = {str(s["nro_boletin"]): s["texto_chunk"] for s in similes}
        path_map      = {str(s["nro_boletin"]): s["patharchivo"] for s in similes}
        nros_semanticos = list(similitud_map.keys())

        if nros_semanticos:
            placeholders = ','.join(['%s'] * len(nros_semanticos))
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                           b.fecha, b.estado, tb.descripcion AS tipo_boletin,
                           b.patharchivo
                    FROM public.boletines b
                    LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                    WHERE b.nro_boletin IN ({placeholders})
                """, nros_semanticos)
                rows_semanticas = cur.fetchall()

            for row in rows_semanticas:
                nro_b_str   = str(row[1])
                id_b        = row[0]
                patharchivo = row[7]
                score       = similitud_map.get(nro_b_str, 0.5)
                texto_chunk = chunk_map.get(nro_b_str, row[3])

                if id_b not in resultados_finales:
                    resultados_finales[id_b] = row[:7] + (score, texto_chunk, frase_busqueda, patharchivo)

    conn.close()

    lista_resultados = list(resultados_finales.values())
    lista_resultados.sort(key=lambda x: x[7], reverse=True)
    return lista_resultados[:top_k]


# ========================
# LECTURA DE PDF DESDE DISCO
# ========================
def obtener_contenido_pdf(nro_boletin: int) -> str:
    nro_str = str(nro_boletin)
    if nro_str in mapa_textos_completos:
        print(f"Recuperación desde caché JSON para boletín #{nro_boletin}.")
        return mapa_textos_completos[nro_str]

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT patharchivo FROM boletines WHERE nro_boletin = %s", (nro_boletin,))
        row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        return None

    nombre_archivo = os.path.basename(row[0].strip())
    ruta_local     = os.path.join(CARPETA_BOLETINES, nombre_archivo)

    if not os.path.exists(ruta_local):
        print(f"PDF no encontrado en disco: {ruta_local}")
        return None

    texto_final = ""
    try:
        paginas_imagenes = convert_from_path(ruta_local, dpi=150)
        with pdfplumber.open(ruta_local) as pdf:
            for i, (page, imagen) in enumerate(zip(pdf.pages, paginas_imagenes)):
                texto_nativo = page.extract_text() or ""
                texto_ocr    = pytesseract.image_to_string(imagen, lang='spa')
                texto_pagina = texto_nativo.strip() + "\n"
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
        id_bd, nro, nro2, desc, fecha, estado, tipo, sim, texto_chunk, palabra_match, patharchivo = b

        contexto += f"""
--- Boletín #{nro} ---
Tipo: {tipo or 'NORMAL'}
Fecha: {fecha or 'Sin fecha'}
Descripción: {desc}
Extracto Relevante: {texto_chunk}
"""
        if any(keyword in pregunta.lower() for keyword in ["resumen", "resumime", "detalle"]) and int(nro) in numeros_pregunta:
            print(f"Cargando texto completo para boletín {nro}...")
            contenido = obtener_contenido_pdf(int(nro))
            if contenido:
                contexto += f"Texto Completo del Documento:\n{contenido[:8000]}\n"

    return contexto


# ========================
# CONSULTA A GROQ
# ========================
def consultar_groq(pregunta: str, contexto: str, historial: list):
    es_busqueda_especifica = not any(k in pregunta.lower() for k in ["resumen", "resumime", "analiza"])

    if es_busqueda_especifica:
        system_instruction = """Sos el motor de búsqueda e IA del Boletín Oficial de Misiones. Tu único objetivo es responder de forma ultra-directa, eficiente y limpia.

REGLAS:
1. Si el usuario busca un término o persona, listá los boletines encontrados de forma directa.
2. Mostrá la información usando exclusivamente este formato limpio para cada uno:
   ### Boletín Nro: [Número] ([Fecha])
   * **Extracto / Contexto exacto:** [Cita breve del fragmento donde aparece el término pedido]
3. NO agregues introducciones largas, textos basura ni conclusiones repetitivas. Sé directo como un buscador indexado.
4. Si no hay coincidencias con el término solicitado, informalo en una sola línea clara."""
    else:
        system_instruction = """Sos un analista experto de boletines de Misiones. El usuario te pidió explícitamente un resumen o análisis detallado.
Estructurá la información de manera ejecutiva usando títulos (##) y secciones claras (Organismos, Personas mencionadas con sus roles, Resoluciones y Resumen Ejecutivo). Evitá texto plano y formateá con viñetas limpias."""

    messages = [{"role": "system", "content": system_instruction}]

    for msg in historial:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({
        "role": "user",
        "content": f"DOCUMENTOS DISPONIBLES:\n{contexto}\n\nCONSULTA: {pregunta}"
    })

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=1500,
            temperature=0.0
        )
        return response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en Groq: {str(e)}")


# ========================
# ENDPOINTS CONSULTA
# ========================
@app.post("/consulta")
def consulta(body: Consulta):
    if not body.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía")

    boletines = buscar_boletines(body.pregunta, body.top_k)
    if not boletines:
        raise HTTPException(status_code=404, detail="No se encontraron boletines relevantes")

    contexto  = construir_contexto(boletines, body.pregunta)
    respuesta = consultar_groq(body.pregunta, contexto, body.historial or [])

    # 1. Mapeamos inicialmente todos los candidatos que encontró el buscador
    boletines_candidatos = [
        {
            "id": b[0],
            "nro_boletin": b[1],
            "tipo": b[6],
            "fecha": str(b[4]) if b[4] else None,
            "similitud": round(b[7], 4),
            "termino_keyword": b[9],
            "url_pdf": construir_url_pdf(b[10]) if b[10] else None
        }
        for b in boletines
    ]

    # 2. FILTRADO: Solo dejamos los chips de los boletines que Groq de verdad listó en su texto de respuesta
    boletines_usados = [
        b for b in boletines_candidatos 
        if f"Boletín Nro: {b['nro_boletin']}" in respuesta or f"#{b['nro_boletin']}" in respuesta
    ]

    # Guardamos estadísticas usando únicamente los boletines que realmente se le mostraron al usuario
    stats = cargar_estadisticas()
    stats["consultas"].append({
        "pregunta": body.pregunta,
        "boletines": [b["nro_boletin"] for b in boletines_usados],
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "hora": datetime.now().strftime("%H:%M")
    })
    guardar_estadisticas(stats)

    return {"respuesta": respuesta, "boletines_usados": boletines_usados}

# ========================
# ENDPOINTS ADMIN
# ========================
@app.post("/admin/login")
def admin_login(body: LoginAdmin):
    if body.usuario == ADMIN_USER and body.password == ADMIN_PASS:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Credenciales incorrectas")


@app.get("/admin/estadisticas")
def admin_estadisticas(usuario: str, password: str):
    if usuario != ADMIN_USER or password != ADMIN_PASS:
        raise HTTPException(status_code=401, detail="No autorizado")

    stats     = cargar_estadisticas()
    consultas = stats.get("consultas", [])

    if not consultas:
        return {"total": 0, "por_dia": {}, "terminos": [], "preguntas_frecuentes": [], "boletines_top": []}

    total   = len(consultas)
    por_dia = Counter(c["fecha"] for c in consultas)

    todas_palabras = []
    for c in consultas:
        palabras = [p.lower() for p in c["pregunta"].split() if len(p) > 3]
        todas_palabras.extend(palabras)
    terminos = Counter(todas_palabras).most_common(20)

    preguntas_frecuentes = Counter(c["pregunta"] for c in consultas).most_common(10)

    todos_boletines = []
    for c in consultas:
        todos_boletines.extend(c.get("boletines", []))
    boletines_top = Counter(todos_boletines).most_common(10)

    return {
        "total": total,
        "por_dia": dict(sorted(por_dia.items())),
        "terminos": [{"termino": t, "cantidad": n} for t, n in terminos],
        "preguntas_frecuentes": [{"pregunta": p, "cantidad": n} for p, n in preguntas_frecuentes],
        "boletines_top": [{"nro_boletin": b, "cantidad": n} for b, n in boletines_top]
    }


@app.get("/health")
def health():
    return {"status": "ok", "modelo": GROQ_MODEL}