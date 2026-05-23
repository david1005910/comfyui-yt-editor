// AI 유튜브 영상 편집기 — 프론트엔드
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const mediaUrl = (cat, file) => `/api/media/${cat}/${encodeURIComponent(file)}`;
const dlUrl = (cat, file) => `/api/download/${cat}/${encodeURIComponent(file)}`;
const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const TRANSITIONS = [
  ["none", "없음(컷)"], ["fade", "페이드"], ["dissolve", "디졸브"],
  ["wipeleft", "와이프←"], ["wiperight", "와이프→"],
  ["slideleft", "슬라이드←"], ["slideright", "슬라이드→"],
  ["circleopen", "원 열기"], ["circleclose", "원 닫기"], ["smoothleft", "부드럽게←"],
];

let tl = [];            // 타임라인 아이템
let startImage = null;  // I2V 시작 이미지
let genMode = "single";
let dragIdx = null;
let clipsCache = [];

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || r.status);
  return r.json();
}

// ───────── 탭 ─────────
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  $$(".panel").forEach((x) => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (t.dataset.tab === "thumb") fillThumbClips();
  if (t.dataset.tab === "proj") loadProjects();
  if (t.dataset.tab === "edit") renderTimeline();
}));

// ───────── 상태 ─────────
async function checkStatus() {
  try {
    const s = await api("/api/status");
    $("#status").innerHTML = s.comfyui
      ? `🟢 ComfyUI 연결됨 · ${s.models.unet}`
      : "🔴 ComfyUI 연결 안 됨 (생성 불가 · 편집/내보내기는 가능)";
    $("#status").className = "status " + (s.comfyui ? "ok" : "bad");
  } catch { $("#status").textContent = "🔴 백엔드 오류"; $("#status").className = "status bad"; }
}

// ───────── 작업 폴링 ─────────
function pollJob(jid, el, onDone) {
  el.classList.remove("hidden", "err");
  el.innerHTML = '⏳ 시작 중…<div class="bar"><i></i></div>';
  const bar = el.querySelector("i");
  const tick = async () => {
    try {
      const j = await api(`/api/jobs/${jid}`);
      el.firstChild.textContent = `⏳ ${j.message}`;
      if (bar) bar.style.width = (j.progress || 0) + "%";
      if (j.status === "done") { el.firstChild.textContent = "✅ " + j.message; if (bar) bar.style.width = "100%"; onDone(j.result); return; }
      if (j.status === "error") { el.innerHTML = "❌ " + esc(j.error || j.message); el.classList.add("err"); return; }
    } catch (e) { el.innerHTML = "❌ " + esc(e.message); el.classList.add("err"); return; }
    setTimeout(tick, 2500);
  };
  tick();
}

// ───────── ① 생성 ─────────
$$(".seg-toggle .mode").forEach((b) => b.addEventListener("click", () => {
  $$(".seg-toggle .mode").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  genMode = b.dataset.mode;
  $("#lenWrap").classList.toggle("hidden", genMode === "long");
  $("#secWrap").classList.toggle("hidden", genMode !== "long");
  $("#longHint").style.display = genMode === "long" ? "block" : "none";
  $("#genBtn").textContent = genMode === "long" ? "긴 영상 생성" : "클립 생성";
}));

$("#startimg").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); fd.append("kind", "image");
  const r = await api("/api/upload", { method: "POST", body: fd });
  startImage = r.filename;
  $("#startimgName").textContent = "✓ " + startImage + " (이미지→영상)";
});

$("#genBtn").addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim();
  if (!prompt) { alert("프롬프트를 입력하세요"); return; }
  const [w, h] = $("#res").value.split("x").map(Number);
  let url, body;
  if (genMode === "long") {
    url = "/api/generate_long";
    body = {
      prompt, negative: $("#negative").value.trim() || null, width: w, height: h,
      seconds: +$("#seconds").value, steps: +$("#steps").value, seed: +$("#seed").value,
      start_image: startImage,
    };
  } else {
    url = "/api/generate";
    body = {
      prompt, negative: $("#negative").value.trim() || null, width: w, height: h,
      length: +$("#len").value, steps: +$("#steps").value, seed: +$("#seed").value,
      start_image: startImage,
    };
  }
  $("#genBtn").disabled = true;
  try {
    const { job_id } = await api(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    pollJob(job_id, $("#genProg"), () => { startImage = null; $("#startimg").value = ""; $("#startimgName").textContent = ""; loadLibrary(); });
  } catch (e) { $("#genProg").classList.remove("hidden"); $("#genProg").textContent = "❌ " + e.message; }
  finally { $("#genBtn").disabled = false; }
});

// ───────── ② 라이브러리 ─────────
$("#upVideo").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); fd.append("kind", "video");
  await api("/api/upload", { method: "POST", body: fd }); e.target.value = ""; loadLibrary();
});
$("#upAudio").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); fd.append("kind", "audio");
  await api("/api/upload", { method: "POST", body: fd }); e.target.value = ""; loadAudio();
});
$("#refreshClips").addEventListener("click", loadLibrary);

async function loadLibrary() {
  const { clips } = await api("/api/clips");
  clipsCache = clips;
  const lib = $("#library");
  if (!clips.length) { lib.innerHTML = '<p class="muted">아직 클립이 없습니다. ①생성에서 만들거나 위에서 업로드하세요.</p>'; return; }
  lib.innerHTML = "";
  for (const c of clips) {
    const d = document.createElement("div"); d.className = "clip";
    d.innerHTML = `
      <video src="${mediaUrl(c.category, c.file)}" muted loop preload="metadata"
             onmouseover="this.play()" onmouseout="this.pause()"></video>
      <div class="clip-name" title="${esc(c.file)}">${esc(c.file)}</div>
      <div class="clip-meta">${c.duration}s · ${(c.size / 1048576).toFixed(1)}MB</div>
      <div class="clip-btns"><button class="add">＋ 타임라인</button><button class="del danger">🗑</button></div>`;
    d.querySelector(".add").addEventListener("click", () => { addClip(c); flashTab("edit"); });
    d.querySelector(".del").addEventListener("click", async () => {
      if (!confirm(`삭제: ${c.file}?`)) return;
      await api(`/api/clips/${encodeURIComponent(c.file)}`, { method: "DELETE" }); loadLibrary();
    });
    lib.appendChild(d);
  }
}

async function loadAudio() {
  const { audio } = await api("/api/audio");
  // 목록 카드
  const box = $("#audioList");
  box.innerHTML = audio.length ? "" : '<p class="muted">업로드된 음악이 없습니다.</p>';
  for (const a of audio) {
    const row = document.createElement("div"); row.className = "audio-row";
    row.innerHTML = `<span>🎵 ${esc(a.file)} <span class="muted small">(${a.duration}s)</span></span>
      <span style="display:flex;gap:8px;align-items:center">
        <audio src="${mediaUrl(a.category, a.file)}" controls preload="none"></audio>
        <button class="del danger">🗑</button></span>`;
    row.querySelector(".del").addEventListener("click", async () => {
      await api(`/api/audio/${encodeURIComponent(a.file)}`, { method: "DELETE" }); loadAudio();
    });
    box.appendChild(row);
  }
  // 내보내기 드롭다운
  const sel = $("#music"); const cur = sel.value;
  sel.innerHTML = '<option value="">(없음)</option>';
  for (const a of audio) {
    const o = document.createElement("option"); o.value = a.file;
    o.textContent = `${a.file} (${a.duration}s)`; sel.appendChild(o);
  }
  sel.value = cur;
}

function flashTab(name) {
  const t = document.querySelector(`.tab[data-tab="${name}"]`);
  if (t) { t.style.transition = "background .2s"; t.style.background = "#2563eb"; setTimeout(() => t.style.background = "", 400); }
}

// ───────── ③ 타임라인 ─────────
function addClip(c) {
  tl.push({
    type: "video", file: c.file, category: c.category, duration: c.duration,
    start: 0, end: c.duration, text: "", caption_pos: "bottom", caption_size: 1.0,
    caption_color: "#ffffff", caption_box: true, volume: 1.0, fade_in: 0, fade_out: 0,
    transition: "none", transition_dur: 0.5,
  });
  renderTimeline();
}
$("#addCard").addEventListener("click", () => {
  tl.push({
    type: "card", card_text: "제목을 입력", card_subtext: "", card_bg: "#101418",
    card_duration: 3.0, fade_in: 0.5, fade_out: 0.5, transition: "fade", transition_dur: 0.5,
  });
  renderTimeline();
});

function transOptions(sel) {
  return TRANSITIONS.map(([v, l]) => `<option value="${v}" ${v === sel ? "selected" : ""}>${l}</option>`).join("");
}

function bindRowInputs(row, item) {
  row.querySelectorAll("[data-k]").forEach((inp) => {
    const ev = inp.type === "checkbox" || inp.tagName === "SELECT" ? "change" : "input";
    inp.addEventListener(ev, () => {
      const k = inp.dataset.k;
      if (inp.type === "checkbox") item[k] = inp.checked;
      else if (inp.type === "number" || inp.type === "range") item[k] = parseFloat(inp.value);
      else item[k] = inp.value;
      if (k === "transition") row.querySelector(".tdur").style.display = inp.value === "none" ? "none" : "";
      updateSummary();
    });
  });
}

function renderTimeline() {
  const el = $("#timeline");
  if (!tl.length) { el.innerHTML = '<p class="muted">②라이브러리에서 "타임라인 추가"를 누르거나, 위 "＋ 타이틀 카드"로 시작하세요.</p>'; updateSummary(); return; }
  el.innerHTML = "";
  tl.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "tl-row" + (s.type === "card" ? " card-row" : "");
    const transSel = i === 0 ? "" : `
        <label>전환(이전→)<select data-k="transition">${transOptions(s.transition)}</select></label>
        <label class="tdur" style="${s.transition === "none" ? "display:none" : ""}">전환초<input type="number" step="0.1" min="0.1" value="${s.transition_dur}" data-k="transition_dur"></label>`;

    if (s.type === "card") {
      row.innerHTML = `
        <div class="tl-handle" title="드래그로 순서변경">☰</div>
        <div class="tl-idx">${i + 1}</div>
        <div class="tl-thumb-card" style="background:${s.card_bg};color:#fff">${esc(s.card_text || "카드")}</div>
        <div class="tl-ctl">
          <div class="tl-name">🪧 타이틀 카드</div>
          <input type="text" placeholder="큰 제목" value="${esc(s.card_text)}" data-k="card_text">
          <input type="text" placeholder="부제(선택)" value="${esc(s.card_subtext)}" data-k="card_subtext">
          <div class="row">
            <label>길이(초)<input type="number" step="0.5" min="0.5" value="${s.card_duration}" data-k="card_duration"></label>
            <label>배경색<input type="color" value="${s.card_bg}" data-k="card_bg"></label>
            <label>페이드인<input type="number" step="0.1" min="0" value="${s.fade_in}" data-k="fade_in"></label>
            <label>페이드아웃<input type="number" step="0.1" min="0" value="${s.fade_out}" data-k="fade_out"></label>
            ${transSel}
          </div>
        </div>
        <div class="tl-btns">
          <button data-a="up">▲</button><button data-a="down">▼</button><button data-a="del" class="danger">✕</button>
        </div>`;
    } else {
      row.innerHTML = `
        <div class="tl-handle" title="드래그로 순서변경">☰</div>
        <div class="tl-idx">${i + 1}</div>
        <video src="${mediaUrl(s.category, s.file)}" muted controls preload="metadata"></video>
        <div class="tl-ctl">
          <div class="tl-name" title="${esc(s.file)}">${esc(s.file)} <span class="muted small">(${s.duration}s)</span></div>
          <div class="row">
            <label>시작(초)<input type="number" step="0.1" min="0" value="${s.start}" data-k="start"></label>
            <label>끝(초)<input type="number" step="0.1" min="0" value="${s.end}" data-k="end"></label>
            ${transSel}
          </div>
          <input type="text" placeholder="자막(선택) — 한글 가능" value="${esc(s.text)}" data-k="text">
          <details class="tl-adv">
            <summary>자막 스타일 · 볼륨 · 페이드</summary>
            <div class="row" style="margin-top:8px">
              <label>자막위치<select data-k="caption_pos">
                <option value="bottom" ${s.caption_pos === "bottom" ? "selected" : ""}>하단</option>
                <option value="center" ${s.caption_pos === "center" ? "selected" : ""}>중앙</option>
                <option value="top" ${s.caption_pos === "top" ? "selected" : ""}>상단</option></select></label>
              <label>자막크기<input type="number" step="0.1" min="0.3" value="${s.caption_size}" data-k="caption_size"></label>
              <label>자막색<input type="color" value="${s.caption_color}" data-k="caption_color"></label>
              <label class="chk"><input type="checkbox" data-k="caption_box" ${s.caption_box ? "checked" : ""}> 자막 배경</label>
            </div>
            <div class="row">
              <label>볼륨<input type="range" min="0" max="2" step="0.05" value="${s.volume}" data-k="volume"></label>
              <label>페이드인<input type="number" step="0.1" min="0" value="${s.fade_in}" data-k="fade_in"></label>
              <label>페이드아웃<input type="number" step="0.1" min="0" value="${s.fade_out}" data-k="fade_out"></label>
            </div>
          </details>
        </div>
        <div class="tl-btns">
          <button data-a="up">▲</button><button data-a="down">▼</button><button data-a="del" class="danger">✕</button>
        </div>`;
    }

    bindRowInputs(row, s);
    row.querySelector('[data-a="up"]').onclick = () => moveItem(i, i - 1);
    row.querySelector('[data-a="down"]').onclick = () => moveItem(i, i + 1);
    row.querySelector('[data-a="del"]').onclick = () => { tl.splice(i, 1); renderTimeline(); };

    // 드래그 정렬
    row.draggable = true;
    row.addEventListener("dragstart", (e) => {
      if (e.target.closest("input,select,textarea,button,details,video,audio")) { e.preventDefault(); return; }
      dragIdx = i; row.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
    });
    row.addEventListener("dragend", () => { row.classList.remove("dragging"); $$(".tl-row").forEach((r) => r.classList.remove("drag-over")); });
    row.addEventListener("dragover", (e) => { e.preventDefault(); row.classList.add("drag-over"); });
    row.addEventListener("dragleave", () => row.classList.remove("drag-over"));
    row.addEventListener("drop", (e) => { e.preventDefault(); row.classList.remove("drag-over"); if (dragIdx !== null && dragIdx !== i) moveItem(dragIdx, i); });

    el.appendChild(row);
  });
  updateSummary();
}

function moveItem(from, to) {
  if (to < 0 || to >= tl.length) return;
  const [it] = tl.splice(from, 1); tl.splice(to, 0, it); renderTimeline();
}

function updateSummary() {
  let total = 0;
  for (const s of tl) {
    let d = s.type === "card" ? (s.card_duration || 0) : ((s.end || 0) - (s.start || 0));
    if (s.transition && s.transition !== "none") d -= (s.transition_dur || 0);
    total += Math.max(0, d);
  }
  $("#tlSummary").textContent = tl.length ? `클립 ${tl.length}개 · 예상 길이 ≈ ${total.toFixed(1)}초` : "";
}

// ───────── 내보내기 ─────────
$("#exportBtn").addEventListener("click", async () => {
  if (!tl.length) { alert("타임라인에 클립을 추가하세요"); return; }
  const [w, h] = $("#exAspect").value.split("x").map(Number);
  const body = {
    name: $("#exName").value || "youtube_export",
    clips: tl.map((s) => ({ ...s, file: s.file || null })),
    music: $("#music").value || null, music_volume: +$("#vol").value,
    music_fade: +$("#musicFade").value, keep_clip_audio: $("#keepAudio").checked,
    width: w, height: h, fps: +$("#exFps").value,
  };
  $("#exResult").classList.add("hidden");
  $("#exportBtn").disabled = true;
  try {
    const { job_id } = await api("/api/export", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    pollJob(job_id, $("#exProg"), (res) => {
      const r = $("#exResult"); r.classList.remove("hidden");
      r.innerHTML = `
        <video src="${mediaUrl(res.category, res.file)}" controls></video>
        <p>완성: ${esc(res.file)} · ${res.duration}s · ${(res.size / 1048576).toFixed(1)}MB</p>
        <a class="primary dl" href="${dlUrl(res.category, res.file)}">⬇ 영상 다운로드</a>`;
    });
  } catch (e) { $("#exProg").classList.remove("hidden"); $("#exProg").textContent = "❌ " + e.message; }
  finally { $("#exportBtn").disabled = false; }
});

// ───────── ④ 썸네일 ─────────
function fillThumbClips() {
  const sel = $("#thumbClip"); const cur = sel.value;
  sel.innerHTML = "";
  for (const c of clipsCache) {
    const o = document.createElement("option"); o.value = c.file; o.textContent = c.file; sel.appendChild(o);
  }
  if (!clipsCache.length) sel.innerHTML = '<option value="">(라이브러리에 클립 없음)</option>';
  sel.value = cur;
}
$("#refreshThumbs").addEventListener("click", loadThumbs);
$("#thumbBtn").addEventListener("click", async () => {
  const file = $("#thumbClip").value;
  if (!file) { alert("원본 클립을 선택하세요(라이브러리에 클립 필요)"); return; }
  const body = {
    file, time: +$("#thumbTime").value, title: $("#thumbTitle").value || null,
    subtitle: $("#thumbSub").value || null, title_color: $("#thumbTitleColor").value,
    bg: $("#thumbBox").value,
  };
  $("#thumbBtn").disabled = true; $("#thumbProg").classList.remove("hidden", "err"); $("#thumbProg").textContent = "⏳ 생성 중…";
  try {
    const res = await api("/api/thumbnail", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    $("#thumbProg").textContent = "✅ 완료";
    const r = $("#thumbResult"); r.classList.remove("hidden");
    r.innerHTML = `<img src="${mediaUrl(res.category, res.file)}?t=${Date.now()}">
      <a class="primary dl" href="${dlUrl(res.category, res.file)}">⬇ 썸네일 다운로드</a>`;
    loadThumbs();
  } catch (e) { $("#thumbProg").classList.add("err"); $("#thumbProg").textContent = "❌ " + e.message; }
  finally { $("#thumbBtn").disabled = false; }
});
async function loadThumbs() {
  const { thumbnails } = await api("/api/thumbnails");
  const g = $("#thumbGallery");
  g.innerHTML = thumbnails.length ? "" : '<p class="muted">아직 썸네일이 없습니다.</p>';
  for (const t of thumbnails) {
    const d = document.createElement("div"); d.className = "clip";
    d.innerHTML = `<img src="${mediaUrl(t.category, t.file)}">
      <div class="clip-meta">${(t.size / 1024).toFixed(0)}KB</div>
      <a class="dl" href="${dlUrl(t.category, t.file)}"><button class="add" style="width:100%">⬇ 다운로드</button></a>`;
    g.appendChild(d);
  }
}

// ───────── 💾 프로젝트 ─────────
$("#projSave").addEventListener("click", async () => {
  const name = $("#projName").value.trim();
  if (!name) { alert("프로젝트 이름을 입력하세요"); return; }
  const data = {
    tl, settings: {
      name: $("#exName").value, aspect: $("#exAspect").value, fps: $("#exFps").value,
      music: $("#music").value, vol: $("#vol").value, musicFade: $("#musicFade").value,
      keepAudio: $("#keepAudio").checked,
    },
  };
  await api("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, data }) });
  loadProjects(); alert("저장됨: " + name);
});
$("#refreshProj").addEventListener("click", loadProjects);
async function loadProjects() {
  const { projects } = await api("/api/projects");
  const box = $("#projList");
  box.innerHTML = projects.length ? "" : '<p class="muted">저장된 프로젝트가 없습니다.</p>';
  for (const p of projects) {
    const row = document.createElement("div"); row.className = "proj-row";
    const dt = new Date(p.updated * 1000).toLocaleString("ko-KR");
    row.innerHTML = `<span><span class="pn">${esc(p.name)}</span> <span class="pd">${dt}</span></span>
      <span class="pb"><button class="load primary">불러오기</button><button class="del danger">삭제</button></span>`;
    row.querySelector(".load").addEventListener("click", () => loadProject(p.name));
    row.querySelector(".del").addEventListener("click", async () => {
      if (!confirm(`삭제: ${p.name}?`)) return;
      await api(`/api/projects/${encodeURIComponent(p.name)}`, { method: "DELETE" }); loadProjects();
    });
    box.appendChild(row);
  }
}
async function loadProject(name) {
  const j = await api(`/api/projects/${encodeURIComponent(name)}`);
  const d = j.data || {};
  tl = d.tl || [];
  const s = d.settings || {};
  if (s.name) $("#exName").value = s.name;
  if (s.aspect) $("#exAspect").value = s.aspect;
  if (s.fps) $("#exFps").value = s.fps;
  if (s.musicFade != null) $("#musicFade").value = s.musicFade;
  if (s.vol != null) $("#vol").value = s.vol;
  if (s.keepAudio != null) $("#keepAudio").checked = s.keepAudio;
  await loadAudio();
  if (s.music) $("#music").value = s.music;
  renderTimeline();
  // 타임라인 탭으로 이동
  document.querySelector('.tab[data-tab="edit"]').click();
  alert("불러옴: " + name);
}

// ───────── 초기화 ─────────
checkStatus(); loadLibrary(); loadAudio();
setInterval(checkStatus, 15000);

// URL 해시로 탭 딥링크 (#lib #edit #thumb #proj)
(function () {
  const h = location.hash.replace("#", "");
  const t = h && document.querySelector(`.tab[data-tab="${h}"]`);
  if (t) t.click();
})();
