# Consultor de Boletines Oficiales — CCPM

Sistema de consulta inteligente sobre boletines oficiales del CCPM de Misiones.
Permite hacer preguntas en lenguaje natural y obtener respuestas basadas en los boletines indexados.

---

## ¿Cómo funciona?

```
Usuario escribe una pregunta
        ↓
sentence-transformers convierte la pregunta en un vector (local)
        ↓
pgvector busca los 5 boletines más similares en PostgreSQL
        ↓
Se arma un prompt con esos boletines como contexto
        ↓
Groq genera la respuesta en lenguaje natural
        ↓
La UI muestra la respuesta + boletines fuente
```

---

## Requisitos previos

Antes de correr el proyecto necesitás tener instalado:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — para correr PostgreSQL con pgvector
- [Python 3.10 o superior](https://www.python.org/downloads/) — para correr la API
- [Git](https://git-scm.com/) — para clonar el repositorio
- Una API key de [Groq](https://console.groq.com) — gratuita, sin tarjeta de crédito

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/FabriC4/boletin-rag.git
cd boletin-rag
```

### 2. Crear el archivo `.env`

Creá un archivo `.env` en la raíz del proyecto con este contenido:

```
DB_HOST=localhost
DB_PORT=5433
DB_NAME=boletin_DB
DB_USER=postgres
DB_PASSWORD=boletin1234
GROQ_API_KEY=tu_api_key_de_groq
GROQ_MODEL=llama-3.1-8b-instant
```

Reemplazá `tu_api_key_de_groq` por tu key de [console.groq.com](https://console.groq.com).

### 3. Levantar la base de datos

```bash
docker compose up -d
```

### 4. Restaurar la base de datos

Necesitás el archivo `dump_plain.sql` (no incluido en el repo por su tamaño).
Una vez que lo tengas en la carpeta del proyecto:

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

Este paso procesa todos los boletines y guarda sus vectores en la BD.
Solo se corre una vez.

```bash
py generar_embeddings.py
```

---

## Uso

### Iniciar el sistema

**Terminal 1 — Base de datos:**
```bash
docker compose up -d
```

**Terminal 2 — API:**
```bash
py -m uvicorn main:app --reload --port 8000
```

### Abrir la interfaz

Abrí el navegador en:
```
http://localhost:8000
```

---

## Estructura del proyecto

```
boletin-rag/
├── static/
│   ├── css/
│   │   └── styles.css        # Estilos de la interfaz
│   └── js/
│       └── chat.js           # Lógica del chat
├── docker-compose.yml        # Configuración de PostgreSQL + pgvector
├── requirements.txt          # Dependencias Python
├── generar_embeddings.py     # Script para indexar los boletines (se corre una vez)
├── main.py                   # API REST con FastAPI
├── index.html                # Interfaz de chat
└── logo.png                  # Logo institucional
```

---

## Stack tecnológico

| Tecnología | Uso |
|---|---|
| PostgreSQL 16 + pgvector | Base de datos con búsqueda vectorial |
| Docker | Contenedor de la base de datos |
| sentence-transformers | Embeddings locales (paraphrase-multilingual-MiniLM-L12-v2) |
| FastAPI | API REST |
| Groq (llama-3.1-8b-instant) | Generación de respuestas en lenguaje natural |
| HTML / CSS / JS | Interfaz de chat institucional |

---

## Costo

El proyecto funciona completamente gratis:
- Docker, Python, FastAPI y sentence-transformers son open source
- Groq ofrece un plan gratuito más que suficiente para desarrollo

---

## Créditos

Desarrollado para la Dirección General del Centro de Cómputos — Provincia de Misiones.
