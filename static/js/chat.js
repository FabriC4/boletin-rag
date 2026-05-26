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
    sender.textContent = role === 'user' ? 'Vos' : 'Asistente';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = content;

    msg.appendChild(sender);
    msg.appendChild(bubble);

    if (sources && sources.length > 0) {
        const sourcesDiv = document.createElement('div');
        sourcesDiv.className = 'sources';

        sources.forEach(s => {
            const chip = document.createElement('div');
            chip.className = 'chip';
            chip.innerHTML = `<i class="ti ti-file-text" style="font-size:11px"></i> Boletín #${s.nro_boletin}`;
            chip.title = `Similitud: ${(s.similitud * 100).toFixed(0)}% | ${s.tipo || ''} | ${s.fecha ? s.fecha.split('T')[0] : ''}`;
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
            body: JSON.stringify({ pregunta, top_k: 5 })
        });

        const data = await res.json();
        removeTyping();

        if (!res.ok) {
            appendMessage('bot', `Error: ${data.detail || 'Error desconocido'}`);
        } else {
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