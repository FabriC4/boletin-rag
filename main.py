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

GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
GROQ_MODEL         = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MODELO_EMBEDDINGS  = "paraphrase-multilingual-MiniLM-L12-v2"
ARCHIVO_EMBEDDINGS = "embeddings.json"
ARCHIVO_TEXTOS     = "textos_boletines.json"
CARPETA_BOLETINES  = "boletines"
ARCHIVO_ESTADISTICAS = "estadisticas.json"
ADMIN_USER         = "admin"
ADMIN_PASS         = "ccpm2026"

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

# Precargar los textos completos extraídos para usarlos como fallback ultra veloz si se consulta por nro de boletín
print("Cargando textos completos extraídos desde JSON...")
if os.path.exists(ARCHIVO_TEXTOS):
    with open(ARCHIVO_TEXTOS, "r", encoding="utf-8") as f:
        lista_textos = json.load(f)
    mapa_textos_completos = {str(t["nro_boletin"]): t["texto"] for t in lista_textos}
    print(f"Textos de boletines precargados: {len(mapa_textos_completos)}")
else:
    mapa_textos_completos = {}
    print("⚠️ Advertencia: textos_boletines.json no encontrado. Las consultas directas por número usarán extracción en caliente.")

groq_client = Groq(api_key=GROQ_API_KEY)


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
    top_k: int = 5

class LoginAdmin(BaseModel):
    usuario: str
    password: str


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


# ========================
# BUSQUEDA SEMANTICA EN JSON (Corregida sin KeyError)
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
            "similitud": float(similitudes[i])  # 
        }
        for i in indices_top
    ]


# ========================
# BUSQUEDA DE BOLETINES
# ========================

# ========================
# MOTOR DE BÚSQUEDA EXPLÍCITA TEXTO POR TEXTO (JSON COMPLETO)
# ========================
def buscar_boletines(pregunta: str, top_k: int):
    # 1. Bypass si el usuario ingresa directamente números de boletín
    numeros = re.findall(r'\b\d{4,}\b', pregunta)
    conn = get_connection()
    
    if numeros:
        placeholders = ','.join(['%s'] * len(numeros))
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                       b.fecha, b.estado, tb.descripcion AS tipo_boletin, 
                       1.0 AS similitud, 'Consulta directa por número' AS texto_chunk
                FROM public.boletines b
                LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                WHERE b.nro_boletin IN ({placeholders})
            """, numeros)
            resultados = cur.fetchall()
        conn.close()
        if resultados:
            return resultados

    resultados_finales = {}
    
    # 2. Limpieza de la consulta para aislar el texto explícito buscado
    palabras_ruido = ["buscar", "buscame", "encuentra", "boletin", "boletines", "que", "hablen", "sobre", "el", "la", "los", "en", "donde", "aparece", "aparecen", "con", "texto", "palabra"]
    palabras_limpias = [p for p in pregunta.split() if p.lower() not in palabras_ruido]
    
    # Intentamos mantener la frase u oración exacta que escribió el usuario
    frase_busqueda = " ".join(palabras_limpias).upper().strip() if palabras_limpias else pregunta.upper().strip()

    # 3. Escaneo literal exhaustivo sobre el contenido de los PDFs (mapa_textos_completos)
    boletines_con_coincidencia = []
    
    if frase_busqueda:
        for nro_str, texto_completo in mapa_textos_completos.items():
            texto_completo_up = texto_completo.upper()
            
            # Matchear la frase u oración explícita
            if frase_busqueda in texto_completo_up:
                boletines_con_coincidencia.append((nro_str, frase_busqueda))
            else:
                # Si es una frase larga y no está exacta, buscamos por la palabra clave más significativa (ej. el apellido)
                palabra_clave = max(palabras_limpias, key=len, default="") if palabras_limpias else ""
                if palabra_clave and palabra_clave.upper() in texto_completo_up:
                    boletines_con_coincidencia.append((nro_str, palabra_clave.upper()))
            
            # Límite holgado para luego ordenar por base de datos
            if len(boletines_con_coincidencia) >= top_k * 3: 
                break

    # 4. Si hay coincidencias explícitas en los textos, extraemos sus metadatos e índices
    if boletines_con_coincidencia:
        numeros_validos = [item[0] for item in boletines_con_coincidencia]
        placeholders = ','.join(['%s'] * len(numeros_validos))
        
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                       b.fecha, b.estado, tb.descripcion AS tipo_boletin
                FROM public.boletines b
                LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                WHERE b.nro_boletin IN ({placeholders})
            """, numeros_validos)
            rows_lexicas = cur.fetchall()
            
            terminos_map = dict(boletines_con_coincidencia)
            
            for row in rows_lexicas:
                nro_b_str = str(row[1])
                termino_encontrado = terminos_map.get(nro_b_str, frase_busqueda)
                
                texto_doc = mapa_textos_completos.get(nro_b_str, "")
                idx = texto_doc.upper().find(termino_encontrado)
                
                # Recorte del fragmento exacto que rodea a la palabra (simula tu web de pruebas)
                inicio = max(0, idx - 250)
                fin = min(len(texto_doc), idx + 450)
                texto_recortado = texto_doc[inicio:fin].strip().replace("\n", " ")

                # =========================================================================
                # RESALTADOR EN NEGRITA PARA MARKDOWN (Compatible con index.html + marked.js)
                # =========================================================================
                if termino_encontrado and len(termino_encontrado) > 2:
                    try:
                        # Reemplaza la palabra encontrada por **palabra** de forma insensible a mayúsculas/minúsculas
                        patron = re.compile(rf"({re.escape(termino_encontrado)})", re.IGNORECASE)
                        texto_recortado = patron.sub(r"**\1**", texto_recortado)
                    except Exception:
                        pass

                fragmento_contexto = "..." + texto_recortado + "..."

                # Forzamos score máximo (1.0) para que tenga prioridad total
                resultados_finales[row[0]] = row + (1.0, fragmento_contexto, termino_encontrado)

    # 5. Fallback semántico (Únicamente si la búsqueda textual estricta dio absolutamente 0 resultados)
    if not resultados_finales:
        print(f"🔍 Sin coincidencias explícitas para '{frase_busqueda}'. Usando fallback semántico por aproximación...")
        similes = buscar_por_similitud(pregunta, top_k)
        
        similitud_map = {str(s["nro_boletin"]): s["similitud"] for s in similes}
        chunk_map = {str(s["nro_boletin"]): s["texto_chunk"] for s in similes}
        nros_semanticos = list(similitud_map.keys())

        if nros_semanticos:
            placeholders = ','.join(['%s'] * len(nros_semanticos))
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT b.id, b.nro_boletin, b.nro_boletin2, b.descripcion,
                           b.fecha, b.estado, tb.descripcion AS tipo_boletin
                    FROM public.boletines b
                    LEFT JOIN public.tipos_boletines tb ON b.tipoboletin_id = tb.id
                    WHERE b.nro_boletin IN ({placeholders})
                """, nros_semanticos)
                rows_semanticas = cur.fetchall()

                for row in rows_semanticas:
                    nro_b_str = str(row[1])
                    id_b = row[0]
                    score_semantico = similitud_map.get(nro_b_str, 0.5)
                    texto_chunk = chunk_map.get(nro_b_str, row[3])
                    
                    # =========================================================================
                    # AQUÍ IBA EL CAMBIO: Sumamos 'frase_busqueda' al final como noveno elemento (b[8])
                    # =========================================================================
                    if id_b not in resultados_finales:
                        resultados_finales[id_b] = row + (score_semantico, texto_chunk, frase_busqueda)

    conn.close()

    # Convertir, ordenar por relevancia y limitar al top_k
    lista_resultados = list(resultados_finales.values())
    lista_resultados.sort(key=lambda x: x[7], reverse=True)
    return lista_resultados[:top_k]

# ========================
# LECTURA DE PDF DESDE DISCO (Optimizado con Fallback Veloz al JSON)
# ========================
def obtener_contenido_pdf(nro_boletin: int) -> str:
    # Intentamos leer desde el mapa en memoria (JSON precargado) para responder instantáneamente sin hacer OCR
    nro_str = str(nro_boletin)
    if nro_str in mapa_textos_completos:
        print(f"⚡ Recuperación instantánea del texto completo para boletín #{nro_boletin} desde caché JSON.")
        return mapa_textos_completos[nro_str]

    # Fallback físico si por alguna razón no estaba indexado en el JSON de textos
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
# CONSTRUCCION DEL CONTEXTO (Corregido con desestructuración limpia)
# ========================
def construir_contexto(boletines, pregunta: str):
    numeros_pregunta = [int(n) for n in re.findall(r'\b\d{4,}\b', pregunta)]
    contexto = ""

    for b in boletines:
        id_bd, nro, nro2, desc, fecha, estado, tipo, sim, texto_chunk = b
        
        contexto += f"""
--- Boletín #{nro} ---
Tipo: {tipo or 'NORMAL'}
Fecha: {fecha or 'Sin fecha'}
Descripción: {desc}
Extracto Relevante: {texto_chunk}
"""
        # Solamente si pide "resumen" o "resumime" y se incluye el número de boletín, cargamos el texto pesado
        if any(keyword in pregunta.lower() for keyword in ["resumen", "resumime", "detalle"]) and int(nro) in numeros_pregunta:
            print(f"Cargando texto completo extendido para solicitud de resumen del boletín {nro}...")
            contenido = obtener_contenido_pdf(int(nro))
            if contenido:
                contexto += f"Texto Completo del Documento:\n{contenido[:8000]}\n"

    return contexto

# ========================
# SYSTEM PROMPT CLARO, DIRECTO Y ECONÓMICO EN TOKENS
# ========================
def consultar_groq(pregunta: str, contexto: str, historial: list):
    # Detectar la intención del usuario
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
            max_tokens=1500,  # Reducido drásticamente para evitar sobrecostos y respuestas interminables
            temperature=0.0   # 0.0 para máxima precisión y cero divagaciones de la IA
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

    # Modificamos la lista para extraer b[8] que guarda la palabra exacta del match
    boletines_usados = [
        {
            "id": b[0],
            "nro_boletin": b[1],
            "tipo": b[6],
            "fecha": str(b[4]) if b[4] else None,
            "similitud": round(b[7], 4),
            "termino_keyword": b[8] if len(b) > 8 else ""  # <--- NUEVO: Enviamos la palabra real a chat.js
        }
        for b in boletines
    ]

    # Registrar estadística
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

    stats    = cargar_estadisticas()
    consultas = stats.get("consultas", [])

    if not consultas:
        return {"total": 0, "por_dia": {}, "terminos": [], "preguntas_frecuentes": [], "boletines_top": []}

    total    = len(consultas)
    por_dia  = Counter(c["fecha"] for c in consultas)

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