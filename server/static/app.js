"use strict";
const $=id=>document.getElementById(id);
const envLabels={sensor:"Sensor",morning:"Pagi",evening:"Sore",night:"Malam",storm:"Badai",bright:"Terang",dark:"Gelap"};
const statusLabels={queued:"Menunggu",delivered:"Dikirim",applied:"Berhasil",expired:"Terlambat",failed:"Belum berhasil",superseded:"Diganti"};
let signedIn=false,draft=[0,0,0],initialized=false,latestState=null,history=[],polling=false;
let lightTest=false,testDark=false,testBright=false;
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
for(let i=0;i<3;i++){
  const card=document.createElement("article");card.className="channel";
  card.innerHTML=`<div class="channel-top"><strong><span id="bulb${i}" class="bulb"></span>Lampu ${i+1}</strong><span class="channel-number">0${i+1}</span></div><p id="confirmed${i}" class="confirmed">—</p><p id="reason${i}" class="reason">Menunggu lampu</p><meter id="level${i}" class="lamp-level" min="0" max="100" value="0" aria-hidden="true"></meter><div id="manual${i}" class="manual-controls" hidden><label for="pwm${i}" class="slider-label">Kecerahan <output id="draft${i}" for="pwm${i}">0%</output></label><input id="pwm${i}" type="range" min="0" max="100" value="0" step="1"><div class="channel-actions"><button id="toggle${i}" class="quiet" type="button">Nyalakan</button><span id="pwm-state${i}" class="pwm-state">Lalu tekan Simpan</span></div></div>`;
  $("channels").appendChild(card);
  $(`pwm${i}`).addEventListener("input",event=>{
    draftVersion++;
    draft[i]=Number(event.target.value);
    $(`draft${i}`).textContent=`${draft[i]}%`;
    $(`toggle${i}`).textContent=draft[i]?"Matikan":"Nyalakan";
  });
  $(`toggle${i}`).addEventListener("click",()=>{draft[i]=draft[i]?0:100;editDraft();});
}
function renderDraft(){
  const manual=$("mode").value==="manual";$("auto-profile").disabled=manual;
  $("control-hint").textContent=manual?"Geser kecerahan tiap lampu, lalu tekan Simpan.":$("auto-profile").value==="light_only"?"Lampu menyala saat gelap dan mati saat terang.":"Saat gelap, kendaraan membuat ketiga lampu terang. Setelah jalan kosong 10 detik, lampu meredup ke 50% dalam 10 detik.";
  for(let i=0;i<3;i++){$(`manual${i}`).hidden=!manual;$(`pwm${i}`).value=draft[i];$(`draft${i}`).textContent=`${draft[i]}%`;$(`pwm${i}`).disabled=!manual;$(`toggle${i}`).disabled=!manual;$(`toggle${i}`).textContent=draft[i]?"Matikan":"Nyalakan";$(`pwm-state${i}`).textContent="Lalu tekan Simpan";}
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
  try{const profile=$("auto-profile").value;await sendCommand({mode:$("mode").value,pwm:[...draft],auto_profile:profile});lightTest=$("mode").value==="auto"&&profile==="light_only";await refresh();}
  catch(error){$("command-message").textContent=error.message;}finally{$("apply").disabled=false;}
});
async function chooseProfile(profile){
  try{await sendCommand({mode:"auto",pwm:[0,0,0],auto_profile:profile});$("mode").value="auto";$("auto-profile").value=profile;renderDraft();lightTest=profile==="light_only";testDark=false;testBright=false;await refresh();}
  catch(error){$("light-test-status").textContent=error.message;}
}
$("start-light-test").addEventListener("click",()=>chooseProfile("light_only"));
$("adaptive-control").addEventListener("click",()=>chooseProfile("adaptive"));
function renderState(state){
  const commandsChanged = latestState?.commands !== state.commands;
  latestState=state;
  const t = state.telemetry;
  const physicalDevice = t?.measurement_kind === "ina219_measured";
  const powerAvailable = t?.ina_valid === true;
  const powerSource = powerAvailable
    ? (physicalDevice ? "Hasil ukur INA219" : "Estimasi dari PWM")
    : "Sensor daya belum siap";
  $("current").textContent = fmt(powerAvailable ? t.current_ma : null, "mA", 2);
  $("power").textContent = fmt(powerAvailable ? t.power_mw : null, "mW", 2);
  $("voltage").textContent = fmt(powerAvailable ? t.voltage_v : null, "V", 3);
  $("sensor-state").textContent = powerSource;
  $("power-source").textContent = powerSource;
  const presetFirmware = ["2.2.0","2.3.0"].includes(t?.firmware_version);
  const pending = state.commands.some(pendingCommand);
  $("device").textContent=state.device_id;$("connection").textContent=state.online?"Terhubung":"Terputus";$("connection").className=`badge ${state.online?"online":"offline"}`;
  $("updated").textContent=`Pembaruan: ${localTime(state.last_seen_ms)}${!state.online&&t?" · data terakhir":""}`;
  $("firmware-state").textContent=t?`Firmware ${t.firmware_version}`:"Belum ada perangkat";
  if(t){
    const dark=t.light_condition==="dark",band=t.light_percent>30&&t.light_percent<45;
    const distance=t.distance_cm?.[0],present=t.vehicle_present?.[0],hold=t.vehicle_hold_ms?.[0]||0;
    $("light").textContent=fmt(t.light_percent,"%");$("ldr-detail").textContent=`ADC ${t.ldr_raw} / 4095 · GPIO34`;
    $("light-condition").textContent=t.light_condition==="unknown"?"Menunggu sensor":`${dark?"Gelap":"Terang"}${band?" · tetap stabil":""}`;
    $("digital-state").textContent=`DO ${t.ldr_digital_dark?"HIGH / gelap":"LOW / terang"}`;
    $("entry-distance").textContent=fmt(distance,"cm");$("entry-presence").textContent=distance==null?"Sensor belum tersedia":present?"Ada kendaraan":hold>0?`Terang ${Math.ceil(hold/1000)} detik lagi`:"Jalan sepi";
    const mode=t.mode==="manual"?"Manual":t.auto_profile==="light_only"?"Otomatis · Ikuti cahaya":"Otomatis · Hemat";
    $("actual-mode").textContent=mode;$("control-state").textContent=t.mode==="manual"?"Manual":"Otomatis";
    $("hero-light").textContent=fmt(t.light_percent,"%");
    $("hero-condition").textContent=t.light_condition==="unknown"?"Menunggu":dark?"Gelap":"Terang";
    $("hero-distance").textContent=fmt(distance,"cm");
    $("hero-presence").textContent=distance==null?"Sensor belum siap":present?"Ada kendaraan":hold>0?"Tetap siaga":"Jalan sepi";
    $("device-status").textContent=state.online?"Terhubung":"Data terakhir";

    $("scene-status").textContent = t.pwm.every(p=>p===0) ? "Semua mati"
      : t.pwm.every(p=>p===100) ? "Semua terang"
      : t.pwm.every(p=>p===50) ? "Redup 50%" : t.pwm.join(" / ")+"%";
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
    const ack=state.commands.find(c=>c.id===t.ack_id);
    if(lightTest&&state.online&&presetFirmware&&ack?.status==="applied"&&t.mode==="auto"&&t.auto_profile==="light_only"){
      const matches=t.light_condition!=="unknown"&&t.pwm.every(v=>v===(dark?100:0));
      if(matches){if(dark)testDark=true;else testBright=true;}
      $("light-test-status").textContent=matches?testDark&&testBright?"Tes selesai. Sensor cahaya bekerja.":"Sudah sesuai. Coba kondisi lainnya.":"Lampu belum sesuai. Periksa pembacaan sensor.";
    }else if(lightTest)$("light-test-status").textContent="Menunggu hasil dari lampu.";
  }
  $("test-dark").textContent=`${testDark?"✓":"○"} Gelap`;$("test-bright").textContent=`${testBright?"✓":"○"} Terang`;
  if (!commandsChanged) return;
  const rows=$("command-history");rows.replaceChildren();
  for(const c of state.commands){const tr=document.createElement("tr"),time=document.createElement("td"),setting=document.createElement("td"),status=document.createElement("td"),pill=document.createElement("span");time.textContent=shortTime(c.issued_at_ms);setting.textContent=`${c.environment?envLabels[c.environment]+" · ":""}${c.mode==="manual"?"Manual "+c.pwm.join("/")+"%":c.auto_profile==="light_only"?"Tes cahaya":"Hemat"}`;setting.title=c.id;pill.className=`status-pill ${c.status}`;pill.textContent=statusLabels[c.status]||c.status;status.appendChild(pill);tr.append(time,setting,status);rows.appendChild(tr);}
  if(!state.commands.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=3;td.textContent="Belum ada aktivitas.";tr.appendChild(td);rows.appendChild(tr);}
  if(state.commands.length){const c=state.commands[0];$("command-message").textContent=c.status==="applied"?"Pengaturan sudah diterapkan.":c.status==="queued"||c.status==="delivered"?"Menunggu lampu menerima pengaturan.":c.status==="failed"?"Belum berhasil. Coba simpan lagi.":c.status==="expired"?"Lampu terlambat merespons. Coba lagi.":"Pengaturan diganti.";}
}
function drawChart(){
  const canvas=$("chart"),ctx=canvas.getContext("2d"),ratio=window.devicePixelRatio||1,width=canvas.clientWidth,height=230;if(!width)return;
  canvas.width=width*ratio;canvas.height=height*ratio;ctx.scale(ratio,ratio);ctx.clearRect(0,0,width,height);
  const left=30,right=10,top=12,bottom=26,w=width-left-right,h=height-top-bottom;ctx.font="10px Segoe UI";ctx.fillStyle="#5d6879";ctx.strokeStyle="#dedad0";
  // The shaded band corresponds to the firmware's 30..45% hysteresis interval.
  ctx.fillStyle="#eee8db";ctx.fillRect(left,top+(1-.45)*h,w,.15*h);ctx.fillStyle="#5d6879";
  for(const value of [0,25,50,75,100]){const y=top+(1-value/100)*h;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(String(value),1,y+4);}
  if(!history.length){$("chart-caption").textContent="Belum ada riwayat.";return;}
  const first=history[0].received_at_ms,last=history.at(-1).received_at_ms,span=Math.max(2000,last-first);
  for(const line of [{color:"#14213d",value:p=>p.light_percent},{color:"#ab8247",value:p=>p.pwm.reduce((a,b)=>a+b,0)/3}]){ctx.strokeStyle=line.color;ctx.lineWidth=2;ctx.beginPath();let previous=null;for(const p of history){const x=left+(p.received_at_ms-first)/span*w,y=top+(1-line.value(p)/100)*h;if(!previous||p.received_at_ms-previous.received_at_ms>6000)ctx.moveTo(x,y);else ctx.lineTo(x,y);previous=p;}ctx.stroke();}
  ctx.fillStyle="#5d6879";ctx.fillText(shortTime(first),left,height-4);const end=shortTime(last);ctx.fillText(end,Math.max(left,width-ctx.measureText(end).width-right),height-4);$("chart-caption").textContent=`${history.length} pembaruan · ${localTime(first)} — ${shortTime(last)}`;
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

window.addEventListener("resize", drawChart);
setInterval(() => { if (signedIn && !liveReady) refresh(); }, 2000);
refresh();
