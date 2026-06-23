# Consultor de Boletines Oficiales — Provincia de Misiones

Sistema de consulta inteligente sobre el Boletín Oficial de la Provincia de Misiones.
Permite hacer preguntas en lenguaje natural y obtener respuestas fundamentadas en los boletines indexados, combinando búsqueda vectorial con un modelo de lenguaje.

---

## ¿Cómo funciona?

```
Usuario escribe una pregunta
        ↓
sentence-transformers convierte la pregunta en un vector (local, sin API)
        ↓
pgvector busca los boletines más similares en PostgreSQL
        ↓  (si la pregunta incluye un número de boletín, se descarga y procesa su PDF)
Se construye un prompt con los boletines como contexto
        ↓
Groq (llama-3.3-70b-versatile) genera la respuesta en lenguaje natural
        ↓
La UI muestra la respuesta + boletines fuente con su similitud
```

El sistema soporta dos modos de búsqueda:

- **Por número de boletín** — si la pregunta contiene un número de 4+ dígitos (ej: `16605`), lo busca directo en la BD y trae el texto completo del PDF.
- **Semántica** — si no hay número, convierte la pregunta en un vector y encuentra los `top_k` boletines más similares por cosine similarity sobre los embeddings de `descripcion`.

---

## Base de datos

El sistema usa **PostgreSQL 17 con pgvector**. Las tablas principales son:

| Tabla | Descripción |
|---|---|
| `boletines` | Tabla central. Contiene número, descripción, fecha, path del PDF y el embedding vectorial |
| `tipos_boletines` | Tipos: `NORMAL` o `SUPLEMENTO` |
| `organismos` | Organismos emisores (código y leyenda) |
| `ordenes_pago` | Órdenes de pago asociadas a publicaciones |

La columna `embedding vector(384)` se agrega manualmente y se popula con `generar_embeddings.py`.
El texto extraído del PDF se guarda en `contenido_pdf` para no repetir descargas.

---

## Preguntas que se le pueden hacer al sistema

### Por número de boletín (trae el PDF completo):
- "¿De qué trata el boletín 16605?"
- "¿A qué organismo pertenece el boletín 16500?"
- "¿Qué resoluciones incluye el boletín 16450?"
- "Resumime el contenido del boletín 16700"
- "¿Qué dice el artículo 1 del boletín 16600?"
- "¿El boletín 16700 está activo y qué fecha tiene?"

### Por tema (búsqueda semántica sobre descripciones):
- "¿Hay boletines sobre designación de personal en Salud Pública?"
- "Buscá boletines relacionados con licitaciones de obra pública"
- "¿Qué boletines hablan de decretos del Ministerio de Hacienda?"
- "Mostrame boletines sobre convenios con municipios"
- "¿Hubo algún boletín sobre creación de cargos docentes?"
- "¿Qué boletines mencionan al Tribunal de Cuentas?"
- "Buscá resoluciones de Vialidad Provincial"
- "¿Hay boletines sobre subastas o remates?"
- "¿Qué boletines incluyen expedientes a sentencia?"
- "Buscá boletines de la Dirección General de Rentas"
- "¿Hubo decretos sobre transporte o aeropuertos?"

---

## Requisitos previos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — para correr PostgreSQL con pgvector
- [Python 3.10 o superior](https://www.python.org/downloads/)
- [Git](https://git-scm.com/)
- API key de [Groq](https://console.groq.com) (gratuita)

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/FabriC4/boletin-rag.git
cd boletin-rag
```

### 2. Crear el archivo `.env`

```env
DB_HOST=localhost
DB_PORT=5433
DB_NAME=boletin_DB
DB_USER=postgres
DB_PASSWORD=boletin1234
GROQ_API_KEY=tu_api_key_de_groq
GROQ_MODEL=llama-3.3-70b-versatile
```

Reemplazar `tu_api_key_de_groq` por la key obtenida en [console.groq.com](https://console.groq.com).

### 3. Levantar la base de datos

```bash
docker compose up -d
```

Esto levanta un contenedor `boletindb` con PostgreSQL 17 + pgvector en el puerto `5433`.

### 4. Restaurar la base de datos

Se necesita el archivo `dump_plain.sql` (no incluido en el repo por su tamaño).
Una vez disponible en la carpeta del proyecto:

```bash
docker cp dump_plain.sql boletindb:/tmp/dump_plain.sql
docker exec -it boletindb psql -U postgres -d boletin_DB -f /tmp/dump_plain.sql
```

### 5. Activar pgvector y agregar columna de embeddings

```bash
docker exec -it boletindb psql -U postgres -d boletin_DB -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker exec -it boletindb psql -U postgres -d boletin_DB -c "ALTER TABLE public.boletines ADD COLUMN IF NOT EXISTS embedding vector(384);"
```

### 6. Instalar dependencias Python

```bash
pip install -r requirements.txt
```

### 7. Generar los embeddings

Procesa todos los boletines y guarda sus vectores en la BD. Solo se corre una vez (o cuando haya boletines nuevos).

```bash
py generar_embeddings.py
```

### 8. (Opcional) Pre-descargar contenido de PDFs

Para poblar el campo `contenido_pdf` en lote antes de que los usuarios hagan consultas:

```bash
py descargar_pdfs.py
```

Este script descarga y extrae el texto de todos los PDFs que aún no tienen contenido cacheado.
También podés monitorear el progreso con:

```bash
docker exec -it boletindb psql -U postgres -d boletin_DB -c "SELECT COUNT(*) FROM boletines WHERE contenido_pdf IS NOT NULL;"
```

---

## Uso

### Iniciar el sistema

**Terminal 1 — Base de datos** (si no está ya levantada):
```bash
docker compose up -d
```

**Terminal 2 — API:**
```bash
py -m uvicorn main:app --reload --port 8000
```

### Abrir la interfaz

```
http://localhost:8000
```

### Endpoint API

`POST /consulta`

```json
{
  "pregunta": "¿Qué resoluciones incluye el boletín 16605?",
  "top_k": 5
}
```

Respuesta:
```json
{
  "respuesta": "El boletín 16605...",
  "boletines_usados": [
    {
      "id": 1234,
      "nro_boletin": 16605,
      "tipo": "NORMAL",
      "fecha": "2026-01-15",
      "similitud": 0.9821
    }
  ]
}
```

---

## Estructura del proyecto

```
boletin-rag/
├── static/
│   ├── css/
│   │   └── styles.css            # Estilos de la interfaz
│   └── js/
│       └── chat.js               # Lógica del chat frontend
├── .env                          # Variables de entorno (no se sube al repo)
├── .gitignore
├── docker-compose.yml            # PostgreSQL 17 + pgvector en puerto 5433
├── generar_embeddings.py         # Indexa descripciones de boletines (correr una vez)
├── descargar_pdfs.py             # Descarga y cachea el texto de los PDFs en lote
├── main.py                       # API REST con FastAPI + lógica RAG
├── index.html                    # Interfaz de chat
└── logo.png                      # Logo institucional
```

---

## Stack tecnológico

| Tecnología | Uso |
|---|---|
| PostgreSQL 17 + pgvector | Base de datos con búsqueda vectorial por cosine similarity |
| Docker | Contenedor de la base de datos |
| sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`) | Embeddings locales multilingüe, sin costo de API |
| FastAPI | API REST |
| httpx + pdfplumber | Descarga y extracción de texto de PDFs |
| Groq (`llama-3.3-70b-versatile`) | Generación de respuestas en lenguaje natural |
| HTML / CSS / JS | Interfaz de chat institucional |

---

## Costo

El proyecto funciona completamente gratis:
- Docker, Python, FastAPI, sentence-transformers y pdfplumber son open source
- Los embeddings se generan localmente (sin API externa)
- Groq ofrece un tier gratuito más que suficiente para desarrollo y uso moderado

---

## Créditos

Desarrollado para la Dirección General del Centro de Cómputos — Provincia de Misiones.
