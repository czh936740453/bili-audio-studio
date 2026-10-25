/* Bili Audio Studio —— 前端逻辑（无框架） */
'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const TOKEN = new URLSearchParams(location.search).get('t') || sessionStorage.getItem('bst_token') || '';
if (TOKEN) sessionStorage.setItem('bst_token', TOKEN);

const S = {
  project: null,
  settings: {},
  ffmpeg: null,
  busy: false,
  jobId: null,
  jobLogIndex: 0,
  logs: [],
  draggedId: null,
  dragging: false,
  audioFor: null,
  outputUrl: '',
  preview: null,
  loopMode: 'off',
  loopTimer: null,
  loopBound: null,
};

/* ---------------- 工具 ---------------- */
async function api(path, options = {}) {
  const opts = Object.assign({ method: 'GET' }, options);
  opts.headers = Object.assign(
    { 'X-Token': TOKEN },
    opts.body ? { 'Content-Type': 'application/json' } : {},
    opts.headers || {}
  );
  if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data || {};
}

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
           : `${m}:${String(s).padStart(2, '0')}`;
}
function fmtDur(sec) {
  const total = Math.round(sec || 0);
  return total >= 3600 ? `${Math.floor(total / 3600)}小时${Math.round((total % 3600) / 60)}分`
                       : `${Math.floor(total / 60)}分${total % 60}秒`;
}
function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
let toastTimer = null;
function toast(message, kind = '') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 3200);
}
function logLine(text, level = 'info') {
  S.logs.push({ text, level, t: new Date().toLocaleTimeString('zh-CN', { hour12: false }) });
  if (S.logs.length > 400) S.logs.splice(0, S.logs.length - 400);
  renderLog();
}
function renderLog() {
  const el = $('#log');
  el.innerHTML = S.logs.slice(-200)
    .map((l) => `<span class="log-line ${l.level}">[${l.t}] ${esc(l.text)}</span>`)
    .join('\n');
  el.scrollTop = el.scrollHeight;
}

/* ---------------- 状态 ---------------- */
async function refresh(silent = true) {
  if (S.dragging) return;   // 拖拽排序时不重绘，避免丢失拖动状态
  try {
    const data = await api('/api/state');
    S.project = data.project;
    S.settings = data.settings || {};
    S.ffmpeg = data.ffmpeg;
    renderAll();
    if (!silent) toast('已刷新');
  } catch (err) {
    if (!silent) toast(err.message, 'error');
  }
}

function renderAll() {
  renderProject();
  renderTracks();
  renderPlayer();
  renderBusy();
}

function renderProject() {
  if (!S.project) return;
  if (document.activeElement !== $('#project-name')) $('#project-name').value = S.project.name || '';
  document.title = `${S.project.name || 'Bili Audio Studio'} · Bili Audio Studio`;
}

/* ---------------- 曲目 ---------------- */
function findTrack(id) {
  return ((S.project && S.project.tracks) || []).find((t) => t.id === id) || null;
}

function outputDuration() {
  const out = S.project && S.project.output;
  return out ? Number(out.duration || 0) : 0;
}

/**
 * 只有在"当前合辑文件"里真正存在的那首才返回定位符。
 * 后加进来、还没合并的曲目返回 null，避免跳到文件末尾（甚至越界）。
 */
function markerForTrack(id) {
  const marker = ((S.project && S.project.markers) || []).find((m) => m.track_id === id) || null;
  const total = outputDuration();
  if (!marker || !total) return null;
  return marker.start < total - 0.05 ? marker : null;
}

function trackPlayable(track) {
  return track.playable === true || track.status === 'done';
}

function activeTrackId() {
  if (S.preview) return S.preview;
  const out = S.project && S.project.output;
  if (!out) return null;
  const idx = activeMarkerIndex($('#audio').currentTime || 0);
  const item = (S.project.markers || [])[idx];
  return item ? item.track_id || null : null;
}

function renderTracks() {
  const list = $('#tracks');
  const tracks = (S.project && S.project.tracks) || [];
  $('#track-count').textContent = String(tracks.length);
  const total = tracks.reduce((sum, t) => sum + (t.duration || 0), 0);
  $('#total-duration').textContent = tracks.length ? `共 ${fmtDur(total)}` : '';
  if (!tracks.length) {
    list.innerHTML = '<li class="empty">还没有曲目。粘贴 B 站链接，点“解析并添加”。</li>';
    return;
  }
  const current = activeTrackId();
  list.innerHTML = tracks.map((t, i) => {
    const marker = markerForTrack(t.id);
    const playable = trackPlayable(t);
    const sub = [
      `<span>${fmtTime(t.duration)}</span>`,
      marker ? `<span>起点 ${fmtTime(marker.start)}</span>` : '',
      t.quality_label ? `<span>${esc(t.quality_label)}</span>` : '',
      playable ? '' : '<span class="pending">待下载</span>',
      t.error ? `<span class="pending" title="${esc(t.error)}">${esc(t.error)}</span>` : '',
    ].filter(Boolean).join('');
    return `<li class="track${current === t.id ? ' active' : ''}" draggable="true" data-id="${esc(t.id)}">
      <span class="handle" title="拖动排序">⋮⋮</span>
      <span class="index">${i + 1}</span>
      <button class="row-play" data-act="play" title="${playable ? '播放这一首' : '还没有下载音频'}"${playable ? '' : ' disabled'}>▶</button>
      <div class="info">
        <div class="t-title" title="${esc(t.part || t.title)}">${esc(t.part || t.title)}</div>
        <div class="t-sub">${sub}</div>
      </div>
      <button class="icon-btn" data-act="up" title="上移">↑</button>
      <button class="icon-btn" data-act="down" title="下移">↓</button>
      <button class="icon-btn" data-act="del" title="删除">✕</button>
    </li>`;
  }).join('');
}

function currentOrder() {
  return ((S.project && S.project.tracks) || []).map((t) => t.id);
}

async function moveTrack(id, delta) {
  const order = currentOrder();
  const index = order.indexOf(id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= order.length) return;
  order.splice(target, 0, order.splice(index, 1)[0]);
  await applyOrder(order);
}

async function applyOrder(order) {
  try {
    const data = await api('/api/tracks/reorder', { method: 'POST', body: { order } });
    S.project = data.project;
    renderAll();
    await syncAudioURL();
  } catch (err) {
    toast(err.message, 'error');
  }
}

async function deleteTrack(id) {
  try {
    const data = await api(`/api/tracks/${id}`, { method: 'DELETE' });
    S.project = data.project;
    renderAll();
    await syncAudioURL();
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ---------------- 解析并自动添加 ---------------- */
async function parseLinks() {
  const raw = $('#urls').value.trim();
  if (!raw) return toast('请先粘贴链接', 'error');
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const btn = $('#btn-parse');
  btn.disabled = true;
  $('#parse-hint').textContent = '解析中…';
  try {
    const data = await api('/api/parse', { method: 'POST', body: { urls: raw } });
    const items = data.items || [];
    const okItems = items.filter((i) => i.ok);
    const failed = items.filter((i) => !i.ok);

    let added = 0;
    let skipped = 0;
    if (okItems.length) {
      const payload = okItems.map((i) => ({
        bvid: i.bvid, aid: i.aid, cid: i.cid, page: i.page,
        title: i.title, part: i.part, owner: i.owner,
        duration: i.duration, source_url: i.source_url,
      }));
      const res = await api('/api/tracks', {
        method: 'POST',
        body: { items: payload, skip_duplicates: true },
      });
      S.project = res.project;
      added = res.added || 0;
      skipped = res.skipped || 0;
    }

    // 解析成功的行从输入框里删掉，只留下失败的
    const okInputs = new Set(okItems.map((i) => i.input));
    const remaining = lines.filter((line) => !okInputs.has(line));
    $('#urls').value = remaining.join('\n');

    const parts = [];
    if (added) parts.push(`新增 ${added} 首`);
    if (skipped) parts.push(`跳过重复 ${skipped} 首`);
    if (failed.length) parts.push(`失败 ${failed.length} 条（已留在输入框）`);
    if (!parts.length) parts.push('没有解析到可添加的曲目');
    $('#parse-hint').textContent = parts.join(' · ');

    if (added) logLine(`已添加 ${added} 首曲目`);
    failed.forEach((f) => logLine(`解析失败：${f.input} — ${f.error}`, 'error'));
    renderAll();
    await syncAudioURL();
  } catch (err) {
    $('#parse-hint').textContent = '';
    toast(err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- 任务 ---------------- */
async function startDownload() {
  try {
    const data = await api('/api/download', { method: 'POST', body: {} });
    beginJob(data.job, '开始下载');
  } catch (err) { toast(err.message, 'error'); }
}

async function startBuild(force) {
  try {
    const data = await api('/api/build', { method: 'POST', body: { force } });
    beginJob(data.job, force ? '开始强制重建' : '开始下载并合并');
  } catch (err) { toast(err.message, 'error'); }
}

function beginJob(job, message) {
  if (!job) return;
  S.jobId = job.id;
  S.jobLogIndex = 0;
  S.busy = true;
  renderBusy();
  logLine(message);
  pollJob();
}

async function pollJob() {
  if (!S.jobId) return;
  try {
    const job = await api(`/api/jobs/${S.jobId}`);
    const logs = job.logs || [];
    for (let i = S.jobLogIndex; i < logs.length; i += 1) logLine(logs[i].text, logs[i].level);
    S.jobLogIndex = logs.length;
    updateJobStatus(job);
    if (job.state === 'running') {
      setTimeout(pollJob, 450);
      return;
    }
    S.busy = false;
    S.jobId = null;
    renderBusy();
    updateJobStatus(null);
    if (job.state === 'done') {
      toast(job.kind === 'build' ? '合并完成' : '下载完成', 'ok');
      logLine(job.kind === 'build' ? '合并完成' : '下载完成', 'ok');
    } else if (job.state === 'cancelled') {
      toast('已取消');
      logLine('任务已取消', 'warn');
    } else {
      toast(job.error || '任务失败', 'error');
      logLine(job.error || '任务失败', 'error');
    }
    if (job.result && job.result.path) logLine(`输出文件：${job.result.path}`);
    await refresh();
    if (job.kind === 'build') await syncAudioURL(true);
  } catch (err) {
    S.busy = false;
    renderBusy();
    toast(err.message, 'error');
  }
}

function updateJobStatus(job) {
  const status = $('#job-status');
  const cancel = $('#btn-cancel');
  if (!job) {
    status.textContent = '';
    cancel.classList.add('hidden');
    return;
  }
  status.textContent = job.message || '处理中…';
  cancel.classList.remove('hidden');
  cancel.disabled = job.state !== 'running';
}

function renderBusy() {
  const busy = S.busy;
  ['#btn-parse', '#btn-download', '#btn-build', '#btn-build-force'].forEach((sel) => {
    const el = $(sel);
    if (el) el.disabled = busy;
  });
  if (!busy) $('#job-status').textContent = '';
  $('#btn-cancel').classList.toggle('hidden', !busy);
  $('#btn-cancel').disabled = !busy;
}

/* ---------------- 定位符 / 播放器 ---------------- */
function markers() {
  return (S.project && S.project.markers) || [];
}

function renderTimelineTicks() {
  const host = $('#tl-markers');
  if (S.preview) { host.innerHTML = ''; return; }
  const total = currentDuration();
  if (!total) { host.innerHTML = ''; return; }
  host.innerHTML = markers()
    .filter((m) => m.start < total - 0.05)
    .map((m, i) => {
      const left = Math.min(99.6, Math.max(0, (m.start / total) * 100));
      return `<div class="tl-tick" data-index="${i}" style="left:${left}%" title="${esc(m.title)}"></div>`;
    }).join('');
}

function currentDuration() {
  const audio = $('#audio');
  if (S.preview) return audio.duration && isFinite(audio.duration) ? audio.duration : 0;
  if (S.project && S.project.output && S.project.output.duration) return S.project.output.duration;
  if (audio.duration && isFinite(audio.duration)) return audio.duration;
  return markers().reduce((sum, m) => sum + (m.duration || 0), 0);
}

function outputVersion() {
  const out = S.project && S.project.output;
  return out ? String(out.built_at || out.size || 0) : '';
}

function syncAudioURL(forceReload = false) {
  const audio = $('#audio');
  if (forceReload) S.preview = null;
  else if (S.preview) return;          // 单曲试听中，不要被整条合辑覆盖
  const out = S.project && S.project.output;
  if (!out || !out.path) {
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    S.audioFor = null;
    S.outputUrl = '';
    renderPlayer();
    return;
  }
  const version = outputVersion();
  if (!forceReload && S.audioFor === version && S.outputUrl) return;
  S.audioFor = version;
  S.outputUrl = `/media/output?t=${encodeURIComponent(TOKEN)}&v=${encodeURIComponent(version)}`;
  audio.src = S.outputUrl;
  audio.load();
  applyLoopUI();
  logLine(`已载入音频：${out.name || ''}`);
  renderPlayer();
}

function renderPlayer() {
  const out = S.project && S.project.output;
  const preview = S.preview ? findTrack(S.preview) : null;
  const audio = $('#audio');
  const dur = currentDuration();
  $('#t-dur').textContent = fmtTime(dur);
  $('#t-cur').textContent = fmtTime(audio.currentTime || 0);
  $('#tl-progress').style.width = dur ? `${Math.min(100, ((audio.currentTime || 0) / dur) * 100)}%` : '0%';
  if (preview) {
    $('#np-title').textContent = `试听：${preview.part || preview.title}`;
    $('#np-sub').textContent = `单曲试听 · ${fmtDur(preview.duration || 0)}`;
    $('#file-hint').textContent = S.project && S.project.output ? S.project.output.path : '';
    $('#btn-play').disabled = false;
    $('#btn-prev').disabled = true;
    $('#btn-next').disabled = true;
  } else if (out && out.path) {
    $('#np-title').textContent = out.name || '合辑音频';
    const count = markers().length;
    $('#np-sub').textContent = `${fmtDur(dur)} · ${count} 个定位符 · ${out.built_at ? new Date(out.built_at * 1000).toLocaleString('zh-CN', { hour12: false }) : ''}`;
    $('#file-hint').textContent = out.path;
    $('#btn-play').disabled = false;
    $('#btn-prev').disabled = false;
    $('#btn-next').disabled = false;
  } else {
    $('#np-title').textContent = '尚未生成音频';
    $('#np-sub').textContent = '添加链接 → 下载并合并';
    $('#file-hint').textContent = '';
    $('#btn-play').disabled = true;
    $('#btn-prev').disabled = true;
    $('#btn-next').disabled = true;
  }
  $('#btn-play').textContent = audio.paused ? '▶' : '❚❚';
}

function seekWhenReady(time) {
  const audio = $('#audio');
  if (audio.readyState >= 1) {
    audio.currentTime = time;
    return;
  }
  audio.addEventListener('loadedmetadata', () => { audio.currentTime = time; }, { once: true });
}

function previewTrack(track) {
  const audio = $('#audio');
  S.preview = track.id;
  audio.src = `/media/track?id=${encodeURIComponent(track.id)}&t=${encodeURIComponent(TOKEN)}&v=${Date.now()}`;
  audio.load();
  applyLoopUI();          // 试听时单曲循环用原生 loop
  audio.play().catch(() => {});
  renderAll();
}

function stopPreview() {
  if (!S.preview) return;
  S.preview = null;
  S.audioFor = null;
  syncAudioURL(true);
}

function playTrack(id) {
  const track = findTrack(id);
  if (!track) return;
  const out = S.project && S.project.output;
  const marker = markerForTrack(id);
  const audio = $('#audio');
  if (out && marker) {
    if (S.preview) {
      S.preview = null;
      S.audioFor = null;
      syncAudioURL(true);
      seekWhenReady(marker.start);
      audio.play().catch((err) => toast(`播放失败：${err.message}`, 'error'));
      armLoopTimer();
    } else {
      jumpTo(marker.start);
      if (audio.paused) audio.play().catch((err) => toast(`播放失败：${err.message}`, 'error'));
    }
    renderAll();
    return;
  }
  if (!trackPlayable(track)) return toast('这首还没有下载音频，先点“仅下载”或“下载并合并”');
  previewTrack(track);
}

function jumpTo(time) {
  const audio = $('#audio');
  const dur = currentDuration();
  if (!S.outputUrl && !S.preview) return toast('还没有生成音频文件', 'error');
  audio.currentTime = Math.max(0, Math.min(time, Math.max(0, dur - 0.05)));
  armLoopTimer();
  renderPlayer();
}

function activeMarkerIndex(time) {
  const data = markers();
  let index = -1;
  for (let i = 0; i < data.length; i += 1) {
    if (time + 0.02 >= data[i].start) index = i; else break;
  }
  return index;
}

function togglePlay() {
  const audio = $('#audio');
  if (!S.outputUrl && !S.preview) return toast('还没有可以播放的音频', 'error');
  if (audio.paused) {
    audio.play().catch((err) => toast(`播放失败：${err.message}`, 'error'));
  } else {
    audio.pause();
  }
}

/* ---------------- 音量 ---------------- */
const ICON_VOLUME = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">'
  + '<path d="M4 9.5h3.2L12 5.6v12.8L7.2 14.5H4z" fill="currentColor"/>'
  + '<path d="M15.3 8.4a5 5 0 010 7.2" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round"/>'
  + '<path d="M17.8 6a8.6 8.6 0 010 12" stroke="currentColor" stroke-width="1.7" fill="none" stroke-linecap="round"/>'
  + '</svg>';
const ICON_MUTED = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">'
  + '<path d="M4 9.5h3.2L12 5.6v12.8L7.2 14.5H4z" fill="currentColor"/>'
  + '<path d="M15.6 9.6l4.6 4.8M20.2 9.6l-4.6 4.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>'
  + '</svg>';

function loadVolumePref() {
  try {
    const raw = localStorage.getItem('bst_volume');
    if (raw === null || raw === '') return 0.8;
    const saved = Number(raw);
    if (isFinite(saved) && saved >= 0 && saved <= 1) return saved;
  } catch (_) { /* 隐私模式等 */ }
  return 0.8;
}

function saveVolumePref(value) {
  try { localStorage.setItem('bst_volume', String(value)); } catch (_) { /* ignore */ }
}

function applyVolumeUI() {
  const audio = $('#audio');
  const pct = Math.round((audio.muted ? 0 : audio.volume) * 100);
  const box = $('#volume-box');
  const slider = $('#volume');
  slider.value = String(pct);
  slider.style.setProperty('--vol', `${pct}%`);
  $('#volume-label').textContent = `${pct}%`;
  $('#btn-mute').innerHTML = audio.muted || audio.volume === 0 ? ICON_MUTED : ICON_VOLUME;
  $('#btn-mute').title = audio.muted ? '取消静音 (M)' : '静音 (M)';
  if (box.classList) box.classList.toggle('muted', audio.muted || audio.volume === 0);
}

function setVolume(value, persist = true) {
  const audio = $('#audio');
  const next = Math.max(0, Math.min(1, Number(value) || 0));
  audio.volume = next;
  if (next > 0 && audio.muted) audio.muted = false;
  if (persist) saveVolumePref(next);
  applyVolumeUI();
}

function toggleMute() {
  const audio = $('#audio');
  audio.muted = !audio.muted;
  if (!audio.muted && audio.volume === 0) audio.volume = 0.5;
  saveVolumePref(audio.muted ? 0 : audio.volume);
  applyVolumeUI();
}

function initVolume() {
  const audio = $('#audio');
  const saved = loadVolumePref();
  audio.volume = saved > 0 ? saved : 0.8;
  audio.muted = saved === 0;
  applyVolumeUI();
  audio.addEventListener('volumechange', applyVolumeUI);
}

function wireVolume() {
  $('#volume').addEventListener('input', (ev) => setVolume(Number(ev.target.value) / 100));
  $('#btn-mute').addEventListener('click', toggleMute);
}

/* ---------------- 循环播放（单曲 / 整体） ---------------- */
const LOOP_MODES = ['off', 'all', 'one'];
const LOOP_TEXT = { off: '顺序播放', all: '列表循环', one: '单曲循环' };

function loadLoopPref() {
  try {
    const value = localStorage.getItem('bst_loop');
    if (LOOP_MODES.includes(value)) return value;
  } catch (_) { /* ignore */ }
  return 'off';
}

function saveLoopPref(mode) {
  try { localStorage.setItem('bst_loop', mode); } catch (_) { /* ignore */ }
}

function applyLoopUI() {
  const btn = $('#btn-loop');
  const audio = $('#audio');
  if (btn) {
    btn.dataset.mode = S.loopMode;
    btn.classList.toggle('active', S.loopMode !== 'off');
    btn.title = `${LOOP_TEXT[S.loopMode]}（L 切换：顺序 → 列表循环 → 单曲循环）`;
  }
  // 单曲试听时用原生循环，整条合辑则靠自己算边界
  audio.loop = S.loopMode === 'one' && !!S.preview;
  if (S.loopMode !== 'one') {
    clearTimeout(S.loopTimer);
    S.loopTimer = null;
    S.loopBound = null;
  }
}

function cycleLoop() {
  const index = LOOP_MODES.indexOf(S.loopMode);
  S.loopMode = LOOP_MODES[(index + 1) % LOOP_MODES.length];
  saveLoopPref(S.loopMode);
  applyLoopUI();
  armLoopTimer();
  toast(`${LOOP_TEXT[S.loopMode]}${S.loopMode === 'one' ? '：当前这首反复播放' : ''}`);
}

/** 当前正在播放的那首在合辑里的起止时间（试听/无合辑时为 null）。 */
function currentTrackBound() {
  if (S.preview) return null;
  const audio = $('#audio');
  const data = markers();
  const index = activeMarkerIndex(audio.currentTime || 0);
  const marker = data[index];
  if (!marker) return null;
  const end = Math.min(
    audio.duration && isFinite(audio.duration) ? audio.duration : Infinity,
    marker.start + marker.duration
  );
  return { start: marker.start, end, trackId: marker.track_id };
}

/** 单曲循环：按当前曲目的结束时间精确回到开头（比 timeupdate 轮询准）。 */
function armLoopTimer() {
  clearTimeout(S.loopTimer);
  S.loopTimer = null;
  S.loopBound = null;
  if (S.loopMode !== 'one' || S.preview) return;
  const audio = $('#audio');
  if (audio.paused) return;
  const bound = currentTrackBound();
  if (!bound || !isFinite(bound.end)) return;
  S.loopBound = bound;
  const remain = (bound.end - audio.currentTime) * 1000;
  if (remain <= 30) {
    audio.currentTime = bound.start;
    return;
  }
  // 边界在排定时就固定下来：回调执行时 currentTime 已到下一首的起点，
  // 重新计算会跳到下一首上去。
  S.loopTimer = setTimeout(() => {
    if (S.loopMode !== 'one' || S.preview) return;
    audio.currentTime = bound.start;
    audio.play().catch(() => {});
    armLoopTimer();
  }, Math.max(60, remain));
}

function playNextPlayable(id) {
  const tracks = (S.project && S.project.tracks) || [];
  const index = tracks.findIndex((t) => t.id === id);
  if (index < 0) return false;
  for (let step = 1; step <= tracks.length; step += 1) {
    const track = tracks[(index + step) % tracks.length];
    if (trackPlayable(track)) {
      playTrack(track.id);
      return true;
    }
  }
  return false;
}

function handleEnded() {
  const audio = $('#audio');
  if (S.preview) {
    if (S.loopMode === 'one' || S.loopMode === 'all') {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    if (playNextPlayable(S.preview)) return;
    renderAll();
    return;
  }
  if (S.loopMode === 'all') {
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  if (S.loopMode === 'one') {
    const bound = currentTrackBound();
    if (bound) {
      audio.currentTime = bound.start;
      audio.play().catch(() => {});
      return;
    }
  }
  renderPlayer();
}

function wireAudio() {
  const audio = $('#audio');
  audio.addEventListener('timeupdate', () => {
    const dur = currentDuration();
    $('#t-cur').textContent = fmtTime(audio.currentTime);
    $('#tl-progress').style.width = dur ? `${Math.min(100, (audio.currentTime / dur) * 100)}%` : '0%';
    const idx = activeMarkerIndex(audio.currentTime);
    const data = markers();
    if (S.preview) {
      const track = findTrack(S.preview);
      $('#marker-now').textContent = track ? `试听 · ${track.part || track.title}` : '试听';
    } else {
      $('#marker-now').textContent = idx >= 0 ? `${idx + 1}/${data.length} · ${data[idx].title}` : '—';
    }
    $$('#tl-markers .tl-tick').forEach((el, i) => el.classList.toggle('active', i === idx));
    const activeId = S.preview || (data[idx] ? data[idx].track_id : null);
    $$('#tracks .track').forEach((el) => el.classList.toggle('active', el.dataset.id === activeId));

    // 单曲循环的兜底检查（计时器被系统节流时也能回到开头）
    if (S.loopMode === 'one' && !S.preview && !audio.paused && S.loopBound) {
      if (audio.currentTime >= S.loopBound.end - 0.12) audio.currentTime = S.loopBound.start;
    }
  });
  audio.addEventListener('play', () => { renderPlayer(); armLoopTimer(); });
  audio.addEventListener('pause', () => { renderPlayer(); clearTimeout(S.loopTimer); });
  audio.addEventListener('seeked', () => armLoopTimer());
  audio.addEventListener('ended', handleEnded);
  audio.addEventListener('loadedmetadata', () => { renderTimelineTicks(); renderPlayer(); armLoopTimer(); });
  audio.addEventListener('error', () => {
    if (S.outputUrl) logLine('音频加载失败，可点击“重新载入”重试', 'error');
  });
  audio.addEventListener('progress', () => {
    try {
      if (audio.buffered.length && audio.duration) {
        const end = audio.buffered.end(audio.buffered.length - 1);
        $('#tl-buffer').style.width = `${(end / audio.duration) * 100}%`;
      }
    } catch (_) { /* ignore */ }
  });
}

/* ---------------- 弹窗 ---------------- */
function openModal(title, html, onSave, okText = '保存') {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal-ok').textContent = okText;
  $('#modal').classList.remove('hidden');
  $('#modal-ok').onclick = async () => {
    try {
      await onSave();
      $('#modal').classList.add('hidden');
    } catch (err) {
      toast(err.message, 'error');
    }
  };
}
function closeModal() { $('#modal').classList.add('hidden'); }

function openSettings() {
  const st = S.settings || {};
  const quality = String(st.audio_id || 0);
  const html = `
    <div class="field">
      <label>B 站 Cookie（SESSDATA，可选）</label>
      <input id="set-sessdata" type="password" placeholder="留空则使用游客身份（一般可拿到 192k）" value="${esc(st.sessdata || '')}">
      <div class="tip">仅保存在本机 workspace/settings.json。填写大会员 SESSDATA 可尝试获取 FLAC/更高码率。</div>
    </div>
    <div class="field">
      <label>音质偏好</label>
      <select id="set-quality">
        <option value="0" ${quality === '0' ? 'selected' : ''}>自动（最高可用）</option>
        <option value="30280" ${quality === '30280' ? 'selected' : ''}>192k（推荐）</option>
        <option value="30232" ${quality === '30232' ? 'selected' : ''}>132k</option>
        <option value="30216" ${quality === '30216' ? 'selected' : ''}>64k（省流量）</option>
      </select>
    </div>
    <div class="field">
      <label>输出格式</label>
      <select id="set-format">
        <option value="m4a" ${(st.format || 'm4a') === 'm4a' ? 'selected' : ''}>M4A / AAC（推荐，通用性好）</option>
        <option value="mp3" ${st.format === 'mp3' ? 'selected' : ''} ${S.ffmpeg && S.ffmpeg.mp3 ? '' : 'disabled'}>MP3${S.ffmpeg && S.ffmpeg.mp3 ? '' : '（当前 ffmpeg 不支持）'}</option>
      </select>
      <div class="tip">两种格式都带定位符（容器章节）+ .cue + .index.json。</div>
    </div>
    <div class="field">
      <label>输出码率</label>
      <select id="set-bitrate">
        ${[128, 192, 256].map((b) => `<option value="${b}" ${Number(st.bitrate_kbps) === b ? 'selected' : ''}>${b} kbps</option>`).join('')}
      </select>
      <div class="tip">拼接时会统一转码为 44.1kHz 立体声 AAC。</div>
    </div>
    <div class="field">
      <label>并发下载数</label>
      <input id="set-parallel" type="number" min="1" max="6" value="${Number(st.parallel) || 3}">
    </div>
    <div class="field">
      <label>输出目录（留空 = workspace/output）</label>
      <input id="set-output" placeholder="例如 D:\\Music\\Bili" value="${esc(st.output_dir || '')}">
    </div>
    <div class="field">
      <label>运行状态</label>
      <div class="tip">${S.ffmpeg && S.ffmpeg.ok
        ? `ffmpeg 就绪：${esc(S.ffmpeg.path)}${S.ffmpeg.mp3 ? '（支持 MP3 导出）' : ''}`
        : '未找到 ffmpeg，请运行 python scripts\\fetch_ffmpeg.py'}</div>
    </div>`;
  openModal('设置', html, async () => {
    const payload = {
      sessdata: $('#set-sessdata').value.trim(),
      audio_id: Number($('#set-quality').value),
      bitrate_kbps: Number($('#set-bitrate').value),
      format: $('#set-format').value,
      parallel: Number($('#set-parallel').value) || 3,
      output_dir: $('#set-output').value.trim(),
    };
    const data = await api('/api/settings', { method: 'POST', body: payload });
    S.settings = data.settings;
    logLine('设置已保存');
    toast('设置已保存', 'ok');
  });
}

async function openProjects() {
  try {
    const data = await api('/api/projects');
    const rows = (data.projects || []).map((p) => `
      <div class="project-row">
        <span class="pname">${esc(p.name)}${p.id === data.current ? ' <span class="badge">当前</span>' : ''}</span>
        <span class="pmeta">${p.tracks} 首</span>
        <button class="ghost" data-open="${esc(p.id)}">打开</button>
      </div>`).join('') || '<div class="empty">还没有项目</div>';
    const html = `<div class="field">
        <label>新建项目</label>
        <div class="row"><input id="new-project-name" placeholder="例如：睡前歌单" style="flex:1">
        <button class="primary" id="btn-create-project">新建</button></div>
      </div>
      <div class="field"><label>已有项目</label>${rows}</div>`;
    openModal('项目', html, async () => { closeModal(); }, '完成');
    $('#modal-ok').style.display = 'none';
    $('#btn-create-project').addEventListener('click', async () => {
      const name = $('#new-project-name').value.trim() || '未命名合辑';
      const res = await api('/api/project', { method: 'POST', body: { action: 'new', name } });
      S.project = res.project;
      S.audioFor = null;
      closeModal();
      renderAll();
      await syncAudioURL(true);
      toast('项目已创建', 'ok');
    });
    $$('[data-open]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const res = await api('/api/project', { method: 'POST', body: { action: 'open', id: btn.dataset.open } });
        S.project = res.project;
        S.audioFor = null;
        closeModal();
        renderAll();
        await syncAudioURL(true);
      });
    });
  } catch (err) {
    toast(err.message, 'error');
  }
}

/* ---------------- 事件绑定 ---------------- */
function wire() {
  $('#btn-parse').addEventListener('click', parseLinks);
  $('#btn-download').addEventListener('click', startDownload);
  $('#btn-build').addEventListener('click', () => startBuild(false));
  $('#btn-build-force').addEventListener('click', () => startBuild(true));
  $('#btn-cancel').addEventListener('click', async () => {
    if (!S.jobId) return;
    try { await api(`/api/jobs/${S.jobId}/cancel`, { method: 'POST', body: {} }); } catch (err) { toast(err.message, 'error'); }
  });
  $('#btn-settings').addEventListener('click', openSettings);
  $('#btn-projects').addEventListener('click', openProjects);
  $('#btn-clear-log').addEventListener('click', () => { S.logs = []; renderLog(); });
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-cancel').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (ev) => { if (ev.target === $('#modal')) closeModal(); });

  $('#project-name').addEventListener('change', async () => {
    const name = $('#project-name').value.trim();
    if (!name) return;
    try {
      const data = await api('/api/project', { method: 'POST', body: { action: 'rename', name } });
      S.project = data.project;
      renderProject();
      logLine(`项目已重命名：${name}`);
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#btn-play').addEventListener('click', togglePlay);
  $('#btn-prev').addEventListener('click', () => step(-1));
  $('#btn-next').addEventListener('click', () => step(1));
  $('#btn-loop').addEventListener('click', cycleLoop);
  $('#btn-reload').addEventListener('click', () => syncAudioURL(true));
  $('#btn-reveal').addEventListener('click', async () => {
    try { await api('/api/reveal', { method: 'POST', body: {} }); } catch (err) { toast(err.message, 'error'); }
  });
  $('#timeline').addEventListener('click', (ev) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
    jumpTo(ratio * currentDuration());
  });

  document.addEventListener('keydown', (ev) => {
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (ev.code === 'Space') { ev.preventDefault(); togglePlay(); }
    else if (ev.key === 'n' || ev.key === 'N') step(1);
    else if (ev.key === 'p' || ev.key === 'P') step(-1);
    else if (ev.key === 'm' || ev.key === 'M') toggleMute();
    else if (ev.key === 'l' || ev.key === 'L') cycleLoop();
    else if (ev.key === 'ArrowRight') jumpTo(($('#audio').currentTime || 0) + 5);
    else if (ev.key === 'ArrowLeft') jumpTo(($('#audio').currentTime || 0) - 5);
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); setVolume($('#audio').volume + 0.05); }
    else if (ev.key === 'ArrowDown') { ev.preventDefault(); setVolume($('#audio').volume - 0.05); }
  });

  // 曲目列表：点行播放、按钮操作、拖拽排序
  const list = $('#tracks');
  list.addEventListener('click', (ev) => {
    const row = ev.target.closest('.track');
    if (!row) return;
    const id = row.dataset.id;
    if (!id) return;
    const btn = ev.target.closest('[data-act]');
    if (!btn) { playTrack(id); return; }
    const act = btn.dataset.act;
    if (act === 'del') deleteTrack(id);
    else if (act === 'up') moveTrack(id, -1);
    else if (act === 'down') moveTrack(id, 1);
    else if (act === 'play') playTrack(id);
  });
  list.addEventListener('dragstart', (ev) => {
    const row = ev.target.closest('.track');
    if (!row) return;
    S.draggedId = row.dataset.id;
    S.dragging = true;
    row.classList.add('dragging');
    ev.dataTransfer.effectAllowed = 'move';
  });
  list.addEventListener('dragend', (ev) => {
    const row = ev.target.closest('.track');
    if (row) row.classList.remove('dragging');
    S.dragging = false;
    $$('.track.drop-target').forEach((el) => el.classList.remove('drop-target'));
  });
  list.addEventListener('dragover', (ev) => {
    ev.preventDefault();
    const row = ev.target.closest('.track');
    $$('.track.drop-target').forEach((el) => el.classList.remove('drop-target'));
    if (row && row.dataset.id !== S.draggedId) row.classList.add('drop-target');
  });
  list.addEventListener('drop', async (ev) => {
    ev.preventDefault();
    const row = ev.target.closest('.track');
    if (!row || !S.draggedId) return;
    const targetId = row.dataset.id;
    if (targetId === S.draggedId) return;
    const order = currentOrder();
    const from = order.indexOf(S.draggedId);
    const to = order.indexOf(targetId);
    if (from < 0 || to < 0) return;
    order.splice(to, 0, order.splice(from, 1)[0]);
    S.draggedId = null;
    await applyOrder(order);
  });

  wireAudio();
  wireVolume();
}

function step(direction) {
  if (S.preview) return;
  const data = markers();
  if (!data.length) return;
  const audio = $('#audio');
  const current = activeMarkerIndex(audio.currentTime || 0);
  let target = current + direction;
  if (direction < 0 && current >= 0 && (audio.currentTime - data[current].start) > 3) target = current;
  target = Math.max(0, Math.min(data.length - 1, target));
  jumpTo(data[target].start);
}

/* ---------------- 启动 ---------------- */
(async function boot() {
  wire();
  initVolume();
  S.loopMode = loadLoopPref();
  applyLoopUI();
  renderLog();
  await refresh();
  await syncAudioURL();
  renderPlayer();
  logLine('界面就绪');
  if (!S.ffmpeg || !S.ffmpeg.ok) {
    toast('未找到 ffmpeg，将无法合并音频', 'error');
    logLine('未找到 ffmpeg，请运行 python scripts\\fetch_ffmpeg.py', 'error');
  }
  setInterval(() => { if (!S.busy) refresh(); }, 2500);
})();
