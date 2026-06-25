import os
import json
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
import multiprocessing

MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
ARCHIVO_TEXTOS = "textos_boletines.json"
ARCHIVO_EMBEDDINGS = "embeddings.json"

# Configuración del Chunking (Segmentación de textos)
CHUNK_SIZE = 1200       # Caracteres por fragmento
CHUNK_OVERLAP = 200     # Caracteres que se repiten entre fragmentos vecinos

def chunk_texto(texto, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Divide un texto largo en fragmentos más pequeños superpuestos."""
    chunks = []
    if not texto:
        return chunks
    
    start = 0
    while start < len(texto):
        end = start + size
        chunks.append(texto[start:end])
        start += (size - overlap)
        
    return chunks

def main():
    if not os.path.exists(ARCHIVO_TEXTOS):
        print(f"❌ Error: No se encontró el archivo {ARCHIVO_TEXTOS}. Primero debes terminar extraer_textos.py")
        return

    print(f"Cargando textos desde {ARCHIVO_TEXTOS}...")
    with open(ARCHIVO_TEXTOS, "r", encoding="utf-8") as f:
        boletines_textos = json.load(f)

    # Cargar embeddings existentes para admitir reanudación si se corta
    if os.path.exists(ARCHIVO_EMBEDDINGS):
        with open(ARCHIVO_EMBEDDINGS, "r", encoding="utf-8") as f:
            embeddings_totales = json.load(f)
        boletines_procesados = {str(e["nro_boletin"]) for e in embeddings_totales}
        print(f"Embeddings previamente calculados encontrados. Boletines ya procesados: {len(boletines_procesados)}")
    else:
        embeddings_totales = []
        boletines_procesados = set()

    # Filtrar boletines que falten procesar
    pendientes = [b for b in boletines_textos if str(b["nro_boletin"]) not in boletines_procesados]
    print(f"Boletines pendientes de vectorizar: {len(pendientes)}")

    if not pendientes:
        print("✅ ¡Todos los embeddings están al día!")
        return

    print(f"Cargando modelo local: {MODELO_EMBEDDINGS}...")
    modelo = SentenceTransformer(MODELO_EMBEDDINGS)

    # 🚀 CONFIGURACIÓN DE MULTIPROCESAMIENTO NATIVO 🚀
    num_cores = max(1, multiprocessing.cpu_count() - 1)
    print(f"Iniciando grupo de multiprocesamiento con {num_cores} núcleos...")
    pool = modelo.start_multi_process_pool(target_devices=["cpu"] * num_cores)

    print("-" * 50)
    print("Preparando fragmentos de texto en memoria...")
    
    # Primero preparamos una lista plana de todos los fragmentos a procesar
    # Esto es necesario para que el multiprocesador trabaje de forma masiva y eficiente
    lista_fragmentos_global = []
    mapeo_metadata = []

    for item in pendientes:
        nro_boletin = item["nro_boletin"]
        path_archivo = item["patharchivo"]
        texto_completo = item["texto"]

        fragmentos = chunk_texto(texto_completo)
        
        for i, frag_texto in enumerate(fragmentos):
            lista_fragmentos_global.append(frag_texto)
            mapeo_metadata.append({
                "nro_boletin": nro_boletin,
                "patharchivo": path_archivo,
                "chunk_id": i,
                "texto_chunk": frag_texto
            })

    if not lista_fragmentos_global:
        print("No se encontraron fragmentos válidos para procesar.")
        modelo.stop_multi_process_pool(pool)
        return

    print(f"Total de fragmentos individuales a vectorizar: {len(lista_fragmentos_global)}")
    print("Generando embeddings en paralelo (esto va a usar tus núcleos al máximo)...")

    try:
        # El modelo codifica en paralelo dividiendo la lista global de fragmentos
        vectores = modelo.encode_multi_process(lista_fragmentos_global, pool, batch_size=128).tolist()

        print("Estructurando resultados...")
        # Volvemos a unir los vectores calculados con sus datos de origen correspondientes
        for meta, vector in zip(mapeo_metadata, vectores):
            embeddings_totales.append({
                "nro_boletin": meta["nro_boletin"],
                "patharchivo": meta["patharchivo"],
                "chunk_id": meta["chunk_id"],
                "texto_chunk": meta["texto_chunk"],
                "vector": vector
            })

    except Exception as e:
        print(f"\n❌ Error durante la generación masiva de embeddings: {e}")
    finally:
        # 🚨 SIEMPRE hay que cerrar el pool de procesos para liberar la memoria de la compu
        modelo.stop_multi_process_pool(pool)

    # Guardado definitivo
    print(f"Guardando resultados en {ARCHIVO_EMBEDDINGS}...")
    with open(ARCHIVO_EMBEDDINGS, "w", encoding="utf-8") as f:
        json.dump(embeddings_totales, f, ensure_ascii=False)

    print(f"\n🎉 ¡Proceso terminado con éxito!")
    print(f"Vectores totales guardados: {len(embeddings_totales)}")

if __name__ == "__main__":
    main()