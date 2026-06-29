const API_URL = 'http://localhost:8000/consulta';
let loading = false;

// ========================
// AUTO RESIZE TEXTAREA
// ========================
function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 100) + 'px';
}

// ========================
// ENTER PARA ENVIAR
// ========================
function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// ========================
// SUGERENCIAS RAPIDAS
// ========================
function setSuggestion(text) {
    const input = document.getElementById('input');
    input.value = text;
    autoResize(input);
    input.focus();
}

// ========================
// AGREGAR MENSAJE AL CHAT
// ========================
function appendMessage(role, content, sources) {
    const empty = document.getElementById('empty-state');
    if (empty) empty.remove();

    const messages = document.getElementById('messages');

    const msg = document.createElement('div');
    msg.className = `msg ${role}`;

    const sender = document.createElement('span');
    sender.className = 'sender';
    sender.textContent = role === 'user' ? 'Usted' : 'Asistente';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    if (role === 'user') {
        bubble.textContent = content;
    } else {
        // 1. Convertimos el Markdown de la respuesta a HTML estructurado
        let htmlRenderizado = marked.parse(content);

        try {
            // 2. Extraemos de forma segura la última pregunta del chat
            const msgUsuario = messages.querySelectorAll('.msg.user .bubble');
            const ultimaPregunta = msgUsuario.length > 0 ? msgUsuario[msgUsuario.length - 1].textContent : "";
            
            // Aislar palabras clave ignorando conectores cortos
            const palabras = ultimaPregunta.split(/\s+/).filter(p => p.length > 3);
            const terminoABuscar = palabras.length > 0 ? palabras[0] : "";

            if (terminoABuscar) {
                // Escapamos caracteres raros y buscamos de forma insensible a mayúsculas/minúsculas
                const regex = new RegExp(`(${terminoABuscar.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
                
                // Al usar $1 nos aseguramos de mantener el formato nativo (si en el PDF está en MAYÚSCULAS, queda en MAYÚSCULAS)
                htmlRenderizado = htmlRenderizado.replace(regex, '<mark class="resaltado-busqueda">$1</mark>');
            }
        } catch (e) {
            console.error("Error al resaltar texto:", e);
        }

        bubble.innerHTML = htmlRenderizado;
    }

    msg.appendChild(sender);
    msg.appendChild(bubble);

    if (sources && sources.length > 0) {
        const sourcesDiv = document.createElement('div');
        sourcesDiv.className = 'sources';

        sources.forEach(s => {
            const chip = document.createElement('div');
            chip.className = 'chip';
            chip.title = `${s.tipo || ''} | ${s.fecha ? s.fecha.split('T')[0] : ''}`;

            const textoContenido = `<i class="ti ti-file-text" style="font-size:11px"></i> Boletín #${s.nro_boletin}`;

            if (s.url_pdf) {
                // Envolvemos todo el interior del chip en el link interactivo para facilitar la descarga
                chip.innerHTML = `
                    <a href="${s.url_pdf}" target="_blank" title="Descargar PDF" 
                       style="color: inherit; text-decoration: none; display: flex; align-items: center; gap: 4px; font-weight: bold;">
                        ${textoContenido} <i class="ti ti-download" style="font-size:11px; margin-left:2px;"></i> [PDF]
                    </a>`;
            } else {
                chip.innerHTML = textoContenido;
            }

            sourcesDiv.appendChild(chip);
        });

        msg.appendChild(sourcesDiv);
    }

    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
}

// ========================
// ANIMACION DE TYPING
// ========================
function showTyping() {
    const empty = document.getElementById('empty-state');
    if (empty) empty.remove();

    const messages = document.getElementById('messages');
    const msg = document.createElement('div');
    msg.className = 'msg bot';
    msg.id = 'typing-indicator';

    const sender = document.createElement('span');
    sender.className = 'sender';
    sender.textContent = 'Asistente';

    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';

    msg.appendChild(sender);
    msg.appendChild(typing);
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
}

function removeTyping() {
    const t = document.getElementById('typing-indicator');
    if (t) t.remove();
}

// ========================
// ENVIAR CONSULTA A LA API
// ========================
const historial = [];

async function sendMessage() {
    if (loading) return;

    const input = document.getElementById('input');
    const pregunta = input.value.trim();
    if (!pregunta) return;

    loading = true;
    document.getElementById('send-btn').disabled = true;
    input.value = '';
    input.style.height = 'auto';

    appendMessage('user', pregunta);
    showTyping();

    try {
        const res = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pregunta, top_k: 5, historial })
        });

        const data = await res.json();
        removeTyping();

        if (!res.ok) {
            appendMessage('bot', `Error: ${data.detail || 'Error desconocido'}`);
        } else {
            // Guardar en historial para memoria de conversación
            historial.push({ role: 'user', content: pregunta });
            historial.push({ role: 'assistant', content: data.respuesta });

            // Mantener solo los últimos 10 mensajes para no saturar el contexto
            if (historial.length > 10) historial.splice(0, 2);

            appendMessage('bot', data.respuesta, data.boletines_usados);
        }

    } catch (err) {
        removeTyping();
        appendMessage('bot', 'No se pudo conectar con la API. Verificá que esté corriendo en localhost:8000.');
    }

    loading = false;
    document.getElementById('send-btn').disabled = false;
    input.focus();
}