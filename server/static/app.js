"use strict";
const $=id=>document.getElementById(id);
const envLabels={sensor:"Sensor",morning:"Pagi",evening:"Sore",night:"Malam",storm:"Badai",bright:"Terang",dark:"Gelap"};
const statusLabels={queued:"Menunggu",delivered:"Dikirim",applied:"Berhasil",expired:"Terlambat",failed:"Belum berhasil",superseded:"Diganti"};
let signedIn=false,draft=[0,0,0],initialized=false,latestState=null,history=[],polling=false;
let liveSocket=null,liveReady=false,commandUpdateTime=0;
let draftVersion=0,savedDraftVersion=0;
const pendingCommand = command => command.status === "queued" || command.status === "delivered";
const fmt=(value,unit,digits=1)=>typeof value==="number"?`${value.toFixed(digits)} ${unit}`:"—";
const localTime=ms=>ms?new Date(ms).toLocaleString("id-ID"):"Belum ada data";
const shortTime=ms=>new Date(ms).toLocaleTimeString("id-ID",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
async function api(path,body){
  const options={credentials:"same-origin",cache:"no-store"};
  if(body!==undefined){options.method="POST";options.headers={"Content-Type":"application/json"};options.body=JSON.stringify(body);}
  const response=await fetch(path,options);
  if(response.status===401&&path!=="/api/login"){showLogin();throw new Error("Sesi berakhir. Silakan masuk kembali.");}
  const result=await response.json();
  if(!response.ok)throw new Error(response.status===401&&path==="/api/login"?"Nama atau kata sandi salah.":typeof result.detail==="string"?result.detail:"Permintaan tidak valid.");
  return result;
}
function showLogin(){
  signedIn = false;
  liveReady = false;
  const socket = liveSocket;
  liveSocket = null;
  if (socket) socket.close();
  latestState = null;
  history = [];
  document.body.classList.add("signed-out");
  $("login").hidden = false;
  $("dashboard").hidden = true;
  $("logout").hidden = true;
  $("navigation").hidden = true;
  $("connection").textContent = "Belum masuk";
  $("connection").className = "badge";
}
function showDashboard(){const entering=!signedIn;document.body.classList.remove("signed-out");signedIn=true;$("login").hidden=true;$("dashboard").hidden=false;$("logout").hidden=false;$("navigation").hidden=false;if(entering)window.scrollTo({top:0,left:0,behavior:"instant"});}
for (let i = 0; i < 3; i++) {
  const card = document.createElement("article");
  card.className = "channel";
  card.innerHTML = `<div class="channel-top">
    <span id="bulb${i}" class="bulb"><svg class="icon" aria-hidden="true"><use href="#icon-logo"></use></svg></span>
    <div><strong class="channel-name">Lampu ${i + 1}</strong><p id="reason${i}" class="reason">Menunggu lampu</p></div>
    <p id="confirmed${i}" class="confirmed">—</p>
  </div>
  <meter id="level${i}" class="lamp-level" min="0" max="100" value="0" aria-hidden="true"></meter>
  <div id="manual${i}" class="manual-controls" hidden>
    <label for="pwm${i}" class="slider-label">Kecerahan <output id="draft${i}" for="pwm${i}" aria-hidden="true">0%</output></label>
    <div id="track${i}" class="slider">
      <div class="slider-track" aria-hidden="true">
        <span class="slider-fill"><span class="slider-particles"></span></span>
      </div>
      <input id="pwm${i}" type="range" min="0" max="100" value="0" step="1" aria-label="Kecerahan lampu ${i + 1}">
    </div>
    <div class="channel-actions">
      <button id="toggle${i}" class="text-button" type="button">Nyalakan</button>
    </div>
  </div>`;
  $("channels").appendChild(card);
  $(`pwm${i}`).addEventListener("input", event => {
    draftVersion++;
    draft[i] = Number(event.target.value);
    renderSlider(i);
  });
  $(`toggle${i}`).addEventListener("click", () => {
    draft[i] = draft[i] ? 0 : 100;
    editDraft();
  });
}

function renderSlider(index) {
  const percent = draft[index];
  $(`draft${index}`).textContent = `${percent}%`;
  $(`track${index}`).style.setProperty("--progress", percent / 100);
  $(`track${index}`).style.setProperty("--fill-opacity", percent ? 1 : 0);
  $(`pwm${index}`).setAttribute("aria-valuetext", `${percent}%`);
  const action = percent ? "Matikan" : "Nyalakan";
  const toggle = $(`toggle${index}`);
  toggle.textContent = action;
  toggle.setAttribute("aria-label", `${action} lampu ${index + 1}`);
}

function renderDraft() {
  const manual = $("mode").value === "manual";
  $("auto-profile").disabled = manual;
  const hint = $("control-hint");
  hint.hidden = manual;
  if (!manual) {
    hint.textContent = $("auto-profile").value === "light_only"
      ? "Lampu menyala saat gelap dan mati saat terang."
      : "Saat gelap, lampu menyala penuh saat kendaraan terdeteksi. Setelah 10 detik jalan sepi, lampu meredup ke 50% dalam 10 detik.";
  }
  for (let i = 0; i < 3; i++) {
    $(`manual${i}`).hidden = !manual;
    const input = $(`pwm${i}`);
    input.value = draft[i];
    input.disabled = !manual;
    $(`toggle${i}`).disabled = !manual;
    renderSlider(i);
  }
}
function editDraft() {
  draftVersion++;
  renderDraft();
}
$("mode").addEventListener("change",editDraft);$("auto-profile").addEventListener("change",editDraft);renderDraft();
$("login").addEventListener("submit",async event=>{
  event.preventDefault();$("login-error").textContent="";const form=event.currentTarget,fields=new FormData(form),button=form.querySelector("button");button.disabled=true;
  try{await api("/api/login",{username:fields.get("username"),password:fields.get("password")});form.elements.password.value="";initialized=false;showDashboard();await refresh();}
  catch(error){$("login-error").textContent=error.message;}finally{button.disabled=false;}
});
$("logout").addEventListener("click",async()=>{try{await api("/api/logout",{});showLogin();}catch(error){$("command-message").textContent=error.message;}});
async function sendCommand(body){
  const version = draftVersion;
  $("command-message").textContent = "Menunggu lampu menerima pengaturan.";
  const command = await api("/api/commands", body);
  savedDraftVersion = version;
  return command;
}
$("apply").addEventListener("click",async()=>{
  $("apply").disabled=true;
  try{const profile=$("auto-profile").value;await sendCommand({mode:$("mode").value,pwm:[...draft],auto_profile:profile});await refresh();}
  catch(error){$("command-message").textContent=error.message;}finally{$("apply").disabled=false;}
});
const gaugeSettings = {
  light: {ceiling: 100, unit: "%", digits: 1},
  "entry-distance": {ceiling: 400, unit: "cm", digits: 1},
  current: {ceiling: 5, unit: "mA", digits: 2, autoScale: true},
  power: {ceiling: 20, unit: "mW", digits: 2, autoScale: true}
};
const gauges = Object.fromEntries(Object.entries(gaugeSettings).map(([id, settings]) => {
  const element = $(id).closest(".gauge");
  return [id, {
    ...settings,
    floor: 0,
    element,
    number: element.querySelector(".gauge-number"),
    unitLabel: element.querySelector(".gauge-unit"),
    scale: element.querySelector(".gauge-scale"),
    track: element.querySelector(".gauge-track"),
    arc: element.querySelector(".gauge-value")
  }];
}));
const scaleLabel = new Intl.NumberFormat("id-ID", {maximumFractionDigits: 1});

function gaugeArc(start, end) {
  if (start >= end) return "";
  const point = fraction => {
    const angle = Math.PI * (1 - fraction);
    const x = 104 + 76 * Math.cos(angle), y = 94 - 76 * Math.sin(angle);
    return `${x.toFixed(2)} ${y.toFixed(2)}`;
  };
  return `M ${point(start)} A 76 76 0 0 1 ${point(end)}`;
}

function renderGauge(id, value) {
  const gauge = gauges[id];
  const reading = Number.isFinite(value) ? value : null;
  if (gauge.reading === reading) return;
  gauge.reading = reading;
  const text = reading === null ? "—" : reading.toFixed(gauge.digits);
  gauge.number.textContent = text;
  gauge.unitLabel.textContent = reading === null ? "" : ` ${gauge.unit}`;
  gauge.element.classList.toggle("has-reading", reading !== null);
  gauge.element.classList.toggle("long-reading", text.length > 7);
  if (reading === null) {
    gauge.arc.setAttribute("d", "");
    gauge.track.setAttribute("d", gaugeArc(0, 1));
    return;
  }

  // Keep the scale stable during a session and expand only when the reading exceeds it.
  if (gauge.autoScale && Math.abs(reading) > gauge.ceiling) {
    const magnitude = Math.abs(reading);
    const order = 10 ** Math.floor(Math.log10(magnitude));
    gauge.ceiling = order * [1, 2, 5, 10].find(step => step * order >= magnitude);
  }
  if (reading < 0 || gauge.floor < 0) gauge.floor = -gauge.ceiling;

  const fraction = (reading - gauge.floor) / (gauge.ceiling - gauge.floor);
  const progress = Math.max(0, Math.min(1, fraction));
  const remainingStart = progress === 0 ? 0 : Math.min(1, progress + .08);
  gauge.arc.setAttribute("d", gaugeArc(0, progress));
  gauge.track.setAttribute("d", gaugeArc(remainingStart, 1));
  const unit = gauge.unit === "%" ? "%" : ` ${gauge.unit}`;
  gauge.scale.textContent = `${scaleLabel.format(gauge.floor)}–${scaleLabel.format(gauge.ceiling)}${unit}`;
}

const lampTiles = [...document.querySelectorAll(".lamp-tile")].map(tile => ({
  tile,
  percent: tile.querySelector(".lamp-percent"),
  state: tile.querySelector(".lamp-state"),
  beam: tile.querySelector(".lamp-emission")
}));

function renderLampOverview(levels, online) {
  const active = levels?.filter(level => level > 0).length;
  $("lamp-summary").textContent = active == null ? "Belum ada data"
    : active === 0 ? "Semua mati" : `${active}/${lampTiles.length} menyala`;
  $("lamp-overview-freshness").hidden = !levels || online;
  for (const [index, lamp] of lampTiles.entries()) {
    const level = levels?.[index] ?? null;
    if (lamp.level === level) continue;
    lamp.level = level;
    lamp.percent.textContent = level === null ? "—" : `${level}%`;
    lamp.state.textContent = level === null ? "Menunggu" : level > 0 ? "Menyala" : "Mati";
    lamp.beam.style.opacity = (level ?? 0) / 100;
    lamp.tile.classList.toggle("is-lit", level > 0);
  }
}

function renderState(state){
  const commandsChanged = latestState?.commands !== state.commands;
  latestState=state;
  const t = state.telemetry;
  renderLampOverview(t?.pwm, state.online);
  const physicalDevice = t?.measurement_kind === "ina219_measured";
  const digitalState = $("digital-state");
  digitalState.hidden = !t || physicalDevice;
  const powerAvailable = t?.ina_valid === true;
  const distance = t?.distance_cm?.[0];
  const current = powerAvailable ? t.current_ma : null;
  const power = powerAvailable ? t.power_mw : null;
  renderGauge("light", t?.light_percent);
  renderGauge("entry-distance", distance);
  renderGauge("current", current);
  renderGauge("power", power);
  const powerSource = powerAvailable
    ? (physicalDevice ? "Hasil ukur INA219" : "Estimasi dari PWM")
    : "Sensor daya belum siap";
  $("voltage").textContent = fmt(powerAvailable ? t.voltage_v : null, "V", 3);
  $("sensor-state").textContent = powerSource;
  $("power-source").textContent = powerSource;
  const pending = state.commands.some(pendingCommand);
  $("device").textContent=state.device_id;$("connection").textContent=state.online?"Terhubung":"Terputus";$("connection").className=`badge ${state.online?"online":"offline"}`;
  $("updated").textContent=`Pembaruan: ${localTime(state.last_seen_ms)}${!state.online&&t?" · data terakhir":""}`;
  $("firmware-state").textContent=t?`Firmware ${t.firmware_version}`:"Belum ada perangkat";
  if(t){
    const dark=t.light_condition==="dark",band=t.light_percent>30&&t.light_percent<45;
    const present=t.vehicle_present?.[0],hold=t.vehicle_hold_ms?.[0]||0;
    $("ldr-detail").textContent=`ADC ${t.ldr_raw} / 4095 · GPIO34`;
    $("light-condition").textContent=t.light_condition==="unknown"?"Menunggu sensor":`${dark?"Gelap":"Terang"}${band?" · tetap stabil":""}`;
    if (!physicalDevice) digitalState.textContent=`DO ${t.ldr_digital_dark?"HIGH / gelap":"LOW / terang"}`;
    $("entry-presence").textContent=distance==null?"Sensor belum tersedia":present?"Ada kendaraan":hold>0?`Terang ${Math.ceil(hold/1000)} detik lagi`:"Jalan sepi";
    const mode=t.mode==="manual"?"Manual":t.auto_profile==="light_only"?"Otomatis · Ikuti cahaya":"Otomatis · Hemat";
    $("actual-mode").textContent=mode;$("control-state").textContent=t.mode==="manual"?"Manual":"Otomatis";

    let reason = "Jalan sepi";
    if (t.mode === "manual") reason = "Pilihan manual";
    else if (!dark) reason = "Cahaya sudah cukup";
    else if (t.auto_profile === "light_only") reason = "Gelap · lampu menyala";
    else if (distance == null) reason = "Sensor perlu diperiksa";
    else if (present) reason = "Ada kendaraan";
    else if (hold > 0) reason = "Tetap terang";
    else if (t.pwm[0] > 50) reason = "Sedang meredup";
    for(let i=0;i<3;i++){
      $(`confirmed${i}`).textContent=`${t.pwm[i]}%`;$(`bulb${i}`).className=`bulb ${t.pwm[i]>0?"on":""}`;$(`level${i}`).value=t.pwm[i];
      $(`reason${i}`).textContent = reason;
    }
    if (!initialized || (draftVersion === savedDraftVersion && !pending)) {
      const formChanged = !initialized || $("mode").value !== t.mode ||
        $("auto-profile").value !== t.auto_profile || draft.some((value, i) => value !== t.pwm[i]);
      if (formChanged) {
        draft = [...t.pwm];
        $("mode").value = t.mode;
        $("auto-profile").value = t.auto_profile;
        initialized = true;
        renderDraft();
      }
    }
  }
  if (!commandsChanged) return;
  const rows=$("command-history");rows.replaceChildren();
  for(const c of state.commands){const tr=document.createElement("tr"),time=document.createElement("td"),setting=document.createElement("td"),status=document.createElement("td"),pill=document.createElement("span");time.textContent=shortTime(c.issued_at_ms);setting.textContent=`${c.environment?envLabels[c.environment]+" · ":""}${c.mode==="manual"?"Manual "+c.pwm.join("/")+"%":c.auto_profile==="light_only"?"Ikuti cahaya":"Hemat"}`;setting.title=c.id;pill.className=`status-pill ${c.status}`;pill.textContent=statusLabels[c.status]||c.status;status.appendChild(pill);tr.append(time,setting,status);rows.appendChild(tr);}
  if(!state.commands.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=3;td.textContent="Belum ada aktivitas.";tr.appendChild(td);rows.appendChild(tr);}
  if(state.commands.length){const c=state.commands[0];$("command-message").textContent=c.status==="applied"?"Pengaturan sudah diterapkan.":c.status==="queued"||c.status==="delivered"?"Menunggu lampu menerima pengaturan.":c.status==="failed"?"Belum berhasil. Coba simpan lagi.":c.status==="expired"?"Lampu terlambat merespons. Coba lagi.":"Pengaturan diganti.";}
}
function drawChart() {
  const canvas = $("chart");
  const width = canvas.clientWidth;
  if (!width) return;

  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const height = canvas.clientHeight;
  const theme = getComputedStyle(canvas);
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  ctx.scale(ratio, ratio);
  const left = 35, right = 9, top = 10, bottom = 24;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const y = value => top + (1 - value / 100) * plotHeight;

  // The shaded band corresponds to the firmware's 30–45% hysteresis interval.
  ctx.fillStyle = theme.getPropertyValue("--chart-band");
  ctx.fillRect(left, y(45), plotWidth, y(30) - y(45));
  ctx.font = '11px Geist, "Segoe UI", sans-serif';
  ctx.fillStyle = theme.getPropertyValue("--ink-muted");
  ctx.strokeStyle = theme.getPropertyValue("--chart-grid");
  ctx.setLineDash([3, 4]);
  for (const value of [0, 25, 50, 75, 100]) {
    ctx.beginPath();
    ctx.moveTo(left, y(value));
    ctx.lineTo(width - right, y(value));
    ctx.stroke();
    ctx.fillText(`${value}%`, 0, y(value) + 4);
  }
  ctx.setLineDash([]);
  if (!history.length) {
    $("chart-caption").textContent = "Belum ada riwayat.";
    return;
  }

  const first = history[0].received_at_ms;
  const last = history.at(-1).received_at_ms;
  const span = Math.max(2000, last - first);
  const x = point => left + (point.received_at_ms - first) / span * plotWidth;
  const series = [
    {color: theme.getPropertyValue("--chart-light"), value: point => point.light_percent},
    {color: theme.getPropertyValue("--amber"), value: point => point.pwm.reduce((sum, value) => sum + value, 0) / 3}
  ];
  ctx.lineWidth = 2.2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const line of series) {
    ctx.strokeStyle = line.color;
    ctx.beginPath();
    let previous = null;
    for (const point of history) {
      const px = x(point), py = y(line.value(point));
      if (!previous || point.received_at_ms - previous.received_at_ms > 6000) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
      previous = point;
    }
    ctx.stroke();
    const latest = history.at(-1);
    ctx.fillStyle = line.color;
    ctx.beginPath();
    ctx.arc(x(latest), y(line.value(latest)), 2.8, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = theme.getPropertyValue("--ink-muted");
  ctx.fillText(shortTime(first), left, height - 4);
  if (last !== first) {
    const end = shortTime(last);
    ctx.fillText(end, width - ctx.measureText(end).width - right, height - 4);
  }
  $("chart-caption").textContent = `${history.length} pembaruan · ${localTime(first)} — ${shortTime(last)}`;
}
function appendHistory(telemetry, receivedAt) {
  if (!telemetry) return;
  const last = history.at(-1);
  if (last?.boot_id === telemetry.boot_id && last.seq >= telemetry.seq) return;
  history.push({...telemetry, received_at_ms:receivedAt});
  if (history.length > 300) history.shift();
  drawChart();
}

async function loadHistory() {
  try {
    const samples = await api("/api/history?limit=300");
    if (!signedIn) return;
    const unique = new Map();
    for (const sample of [...samples, ...history]) {
      unique.set(`${sample.boot_id}:${sample.seq}`, sample);
    }
    history = [...unique.values()].sort((a,b)=>a.received_at_ms-b.received_at_ms).slice(-300);
    drawChart();
  } catch {
    // The live view remains available if the history request fails.
  }
}

function acceptSnapshot(state) {
  commandUpdateTime = state.server_time_ms;
  renderState(state);
  appendHistory(state.telemetry, state.last_seen_ms);
}

function receiveLive(message) {
  if (message.type === "snapshot") {
    liveReady = true;
    acceptSnapshot(message.state);
    loadHistory();
    return;
  }
  if (!liveReady || !latestState) return;
  const state = {...latestState};
  state.server_time_ms = Math.max(state.server_time_ms, message.server_time_ms);
  if (message.telemetry && message.last_seen_ms >= (state.last_seen_ms ?? 0)) {
    const olderSequence = state.telemetry?.boot_id === message.telemetry.boot_id &&
      state.telemetry.seq >= message.telemetry.seq;
    if (!olderSequence) {
      state.telemetry = message.telemetry;
      state.last_seen_ms = message.last_seen_ms;
      appendHistory(state.telemetry, state.last_seen_ms);
    }
  }
  if (message.commands && message.server_time_ms >= commandUpdateTime) {
    state.commands = message.commands;
    commandUpdateTime = message.server_time_ms;
  }
  state.online = state.last_seen_ms != null && state.server_time_ms - state.last_seen_ms < 15000;
  renderState(state);
  if (message.type === "heartbeat" && state.commands.some(
    command => pendingCommand(command) && command.expires_at_ms <= state.server_time_ms
  )) refresh(true);
}

function connectLive() {
  if (!signedIn || liveSocket) return;
  const url = new URL("/api/live", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url);
  liveSocket = socket;
  socket.addEventListener("message", event => {
    if (liveSocket === socket) receiveLive(JSON.parse(event.data));
  });
  socket.addEventListener("close", () => {
    if (liveSocket !== socket) return;
    liveSocket = null;
    liveReady = false;
  });
}

async function refresh(force = false) {
  if (liveReady && !force) {
    if (latestState) renderState(latestState);
    return;
  }
  if (polling) return;
  polling = true;
  try {
    const state = await api("/api/state");
    const firstLoad = !signedIn;
    showDashboard();
    if (!liveReady || state.server_time_ms >= latestState.server_time_ms) acceptSnapshot(state);
    if (firstLoad) loadHistory();
    connectLive();
  } catch(error) {
    if (signedIn) {
      $("connection").textContent = "Koneksi terputus";
      $("connection").className = "badge offline";
      $("updated").textContent = `${error.message} · data terakhir ${localTime(latestState?.last_seen_ms)}`;
    }
  } finally {
    polling = false;
  }
}

function highlightSection() {
  const links = [...$("navigation").querySelectorAll("a")];
  const active = links.find(link => link.hash === window.location.hash) ?? links[0];
  for (const link of links) {
    if (link === active) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}

window.addEventListener("hashchange", highlightSection);
highlightSection();
window.addEventListener("resize", drawChart);
setInterval(() => { if (signedIn && !liveReady) refresh(); }, 2000);
refresh();
