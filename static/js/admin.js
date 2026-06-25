let credenciales = { usuario: '', password: '' };

async function doLogin() {
    const usuario  = document.getElementById('user-input').value.trim();
    const password = document.getElementById('pass-input').value.trim();

    try {
        const res = await fetch('/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ usuario, password })
        });

        if (!res.ok) {
            document.getElementById('error-msg').style.display = 'block';
            return;
        }

        credenciales = { usuario, password };
        document.getElementById('login-wrap').style.display  = 'none';
        document.getElementById('dashboard').style.display   = 'block';
        document.getElementById('btn-logout').style.display  = 'flex';
        cargarEstadisticas();

    } catch(e) {
        document.getElementById('error-msg').style.display = 'block';
    }
}

async function cargarEstadisticas() {
    const res  = await fetch(`/admin/estadisticas?usuario=${credenciales.usuario}&password=${credenciales.password}`);
    const data = await res.json();

    // Totales
    document.getElementById('total-consultas').textContent    = data.total;
    const hoy = new Date().toISOString().split('T')[0];
    document.getElementById('consultas-hoy').textContent      = data.por_dia[hoy] || 0;
    document.getElementById('boletines-distintos').textContent = data.boletines_top.length;

    // Gráfico por día
    const porDia  = data.por_dia;
    const dias    = Object.keys(porDia);
    const valores = Object.values(porDia);
    const maxVal  = Math.max(...valores, 1);
    const chartEl = document.getElementById('chart-bars');
    chartEl.innerHTML = '';
    dias.slice(-14).forEach(dia => {
        const val    = porDia[dia];
        const altura = Math.max((val / maxVal) * 70, 2);
        const fechaCorta = dia.slice(5);
        chartEl.innerHTML += `
            <div class="bar-wrap" title="${dia}: ${val} consultas">
                <span class="bar-count">${val}</span>
                <div class="bar" style="height:${altura}px"></div>
                <span class="bar-label">${fechaCorta}</span>
            </div>`;
    });

    // Preguntas frecuentes
    document.getElementById('tabla-preguntas').innerHTML =
        data.preguntas_frecuentes.map((p, i) => `
            <tr>
                <td><span class="badge">${i+1}</span></td>
                <td>${p.pregunta}</td>
                <td><span class="badge">${p.cantidad}</span></td>
            </tr>`).join('');

    // Términos
    document.getElementById('tabla-terminos').innerHTML =
        data.terminos.map((t, i) => `
            <tr>
                <td><span class="badge">${i+1}</span></td>
                <td>${t.termino}</td>
                <td><span class="badge">${t.cantidad}</span></td>
            </tr>`).join('');

    // Boletines top
    document.getElementById('tabla-boletines').innerHTML =
        data.boletines_top.map((b, i) => `
            <tr>
                <td><span class="badge">${i+1}</span></td>
                <td><strong>#${b.nro_boletin}</strong></td>
                <td><span class="badge">${b.cantidad}</span></td>
            </tr>`).join('');
}

function logout() {
    credenciales = { usuario: '', password: '' };
    document.getElementById('login-wrap').style.display  = 'flex';
    document.getElementById('dashboard').style.display   = 'none';
    document.getElementById('btn-logout').style.display  = 'none';
    document.getElementById('user-input').value = '';
    document.getElementById('pass-input').value = '';
}