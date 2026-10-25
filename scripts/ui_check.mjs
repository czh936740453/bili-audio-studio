/**
 * 界面运行时自检：用最小 DOM 替身在 Node 里真实执行 app.js。
 * 目的：在没有浏览器（沙箱禁止命名管道）时，仍能发现 boot()/wire() 里的
 * 语法错误、引用错误、空元素访问等会导致白屏的问题。
 *
 * 用法： node scripts/ui_check.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const appJs = readFileSync(join(here, '..', 'biliaudio', 'web', 'app.js'), 'utf8');

const errors = [];
const calls = { fetch: [], listeners: 0, play: 0 };
const timers = [];
let timerSeq = 0;

function makeEl(name) {
  const store = {};
  const handlers = {};
  const el = {
    dataset: {},
    style: {
      setProperty(key, value) { this[key] = value; },
      removeProperty() {},
      getPropertyValue: () => '',
    },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    children: [],
    value: '',
    textContent: '',
    innerHTML: '',
    disabled: false,
    scrollTop: 0,
    scrollHeight: 0,
    paused: true,
    currentTime: 0,
    duration: 0,
    buffered: { length: 0, end: () => 0 },
    addEventListener(type, fn) {
      calls.listeners += 1;
      handlers[type] = handlers[type] || [];
      handlers[type].push(fn);
    },
    removeEventListener() {},
    /** 测试用：触发注册过的事件 */
    trigger(type, ev = { target: { tagName: 'button' }, currentTarget: null }) {
      return Promise.all((handlers[type] || []).map((fn) => fn(ev)));
    },
    appendChild() {},
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    play: () => { calls.play += 1; return Promise.resolve(); },
    pause() {},
    load() {},
    getBoundingClientRect: () => ({ left: 0, width: 100 }),
    removeAttribute() {},
  };
  return new Proxy(el, {
    get(target, prop) {
      if (prop in store) return store[prop];
      if (prop in target) return target[prop];
      return undefined;
    },
    set(target, prop, value) {
      store[prop] = value;
      return true;
    },
  });
}

const elements = new Map();
const getEl = (sel) => {
  if (!elements.has(sel)) elements.set(sel, makeEl(sel));
  return elements.get(sel);
};

const documentStub = {
  activeElement: null,
  title: '',
  querySelector: getEl,
  querySelectorAll: () => [],
  addEventListener() { calls.listeners += 1; },
  createElement: () => makeEl('div'),
  body: makeEl('body'),
};

const statePayload = {
  project: {
    id: 'p1', name: '自检合辑', tracks: [
      { id: 't1', bvid: 'BV1', cid: 1, title: 'A', part: 'A', duration: 60, status: 'done', quality_label: '192k' },
      { id: 't2', bvid: 'BV2', cid: 2, title: 'B', part: 'B', duration: 90, status: 'new', error: '' },
    ],
    markers: [
      { track_id: 't1', title: 'A', start: 0, duration: 60 },
      { track_id: 't2', title: 'B', start: 60, duration: 90 },
    ],
    output: { path: 'C:/tmp/x.m4a', name: 'x.m4a', duration: 150, size: 1024, built_at: 1700000000 },
    build: { state: 'done' },
  },
  jobs: [],
  ffmpeg: { ok: true, path: 'ffmpeg.exe', version: 'ffmpeg 9', mp3: true, ffprobe: true },
  settings: { sessdata: '', audio_id: 0, bitrate_kbps: 192, format: 'm4a', parallel: 3, output_dir: '' },
};

const sandbox = {
  console,
  document: documentStub,
  location: { search: '?t=test-token', href: 'http://127.0.0.1/x' },
  sessionStorage: { getItem: () => 'test-token', setItem() {} },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  storageEvent: null,
  URLSearchParams,
  setTimeout: (fn, delay = 0) => {
    if (typeof fn === 'function') {
      timerSeq += 1;
      timers.push({ id: timerSeq, fn, delay });
      return timerSeq;
    }
    return 0;
  },
  clearTimeout: (id) => {
    const index = timers.findIndex((t) => t.id === id);
    if (index >= 0) timers.splice(index, 1);
  },
  setInterval: () => 0,
  clearInterval() {},
  fetch: async (url, opts = {}) => {
    calls.fetch.push(url);
    let payload = { ok: true };
    if (url.includes('/api/state')) payload = statePayload;
    if (url.includes('/api/parse')) {
      payload = {
        items: [
          { ok: true, input: 'BV1GJ411x7h7', bvid: 'BV1GJ411x7h7', aid: 1, cid: 11, page: 1,
            title: '新歌', part: '新歌', owner: 'up', duration: 30, source_url: 'u', display: '新歌' },
          { ok: false, input: '不是链接', error: '无法识别的链接' },
        ],
      };
    }
    if (url.includes('/api/tracks/reorder')) payload = { project: statePayload.project };
    else if (url.includes('/api/tracks')) {
      payload = {
        project: {
          ...statePayload.project,
          tracks: [
            ...statePayload.project.tracks,
            { id: 't3', bvid: 'BV1GJ411x7h7', cid: 11, title: '新歌', part: '新歌',
              duration: 30, status: 'new', playable: false, quality_label: '' },
          ],
          markers: [...statePayload.project.markers, { track_id: 't3', title: '新歌', start: 150, duration: 30 }],
        },
        added: 1,
        skipped: 0,
      };
    }
    if (url.includes('/api/jobs')) {
      payload = { id: 'j1', kind: 'build', state: 'done', progress: 1, message: '完成', logs: [], result: {} };
    }
    if (url.includes('/api/projects')) payload = { projects: [], current: 'p1' };
    return {
      ok: true,
      status: 200,
      json: async () => payload,
    };
  },
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;

process.on('uncaughtException', (err) => errors.push(`uncaught: ${err.message}`));
process.on('unhandledRejection', (err) => errors.push(`unhandled: ${err && err.message ? err.message : err}`));

try {
  vm.runInNewContext(appJs, sandbox, { filename: 'app.js' });
} catch (err) {
  errors.push(`同步执行失败: ${err.message}`);
}

await new Promise((resolve) => setImmediate(resolve));
await new Promise((resolve) => setImmediate(resolve));

const checks = [];
const note = (name, ok, detail = '') => {
  checks.push({ name, ok, detail });
  console.log(`  [${ok ? '✓' : '✗'}] ${name}${detail ? ' — ' + detail : ''}`);
};

console.log('\nUI 运行时自检');
const trackHtml = String(getEl('#tracks').innerHTML);
note('脚本可执行且无异常', errors.length === 0, errors.join('; '));
note('注册了 DOM 事件', calls.listeners > 10, `${calls.listeners} 个`);
note('启动即请求了 /api/state', calls.fetch.some((u) => u.includes('/api/state')));
note('列出了曲目行', trackHtml.includes('data-id="t1"') && trackHtml.includes('data-id="t2"'));
note('每行都有播放按钮', (trackHtml.match(/data-act="play"/g) || []).length === 2);
note('未就绪曲目的播放按钮被禁用', trackHtml.includes('还没有下载音频'));
note('播放器标题已填充', String(getEl('#np-title').textContent).length > 0,
  String(getEl('#np-title').textContent));
note('载入了合并音频地址', String(getEl('#audio').src || '').includes('/media/output'),
  String(getEl('#audio').src || ''));
note('音量滑杆已同步', String(getEl('#volume').value) === '80', `value=${getEl('#volume').value}`);
note('音量百分比已显示', String(getEl('#volume-label').textContent) === '80%',
  String(getEl('#volume-label').textContent));
note('静音按钮已渲染图标', String(getEl('#btn-mute').innerHTML).includes('<svg'));
const loopBtn = getEl('#btn-loop');
note('循环按钮默认顺序播放', String(loopBtn.dataset.mode) === 'off');
await loopBtn.trigger('click');
const loopAll = String(loopBtn.dataset.mode);
await loopBtn.trigger('click');
const loopOne = String(loopBtn.dataset.mode);
await loopBtn.trigger('click');
const loopOff = String(loopBtn.dataset.mode);
note('循环按钮三态切换', `${loopAll}/${loopOne}/${loopOff}` === 'all/one/off', `${loopAll} → ${loopOne} → ${loopOff}`);

// ---- 循环行为 ----
const audioEl = getEl('#audio');
const hasFns = typeof sandbox.armLoopTimer === 'function' && typeof sandbox.handleEnded === 'function';
note('循环控制函数可用', hasFns);
if (hasFns) {
  await loopBtn.trigger('click');            // off -> all
  note('切到整体循环', String(loopBtn.dataset.mode) === 'all', String(loopBtn.dataset.mode));
  audioEl.duration = 150;
  audioEl.currentTime = 150;
  audioEl.paused = true;
  const playBefore = calls.play;
  sandbox.handleEnded();
  note('整体循环：播完回到 0:00 继续', audioEl.currentTime === 0 && calls.play > playBefore,
    `t=${audioEl.currentTime}, play=${calls.play - playBefore}`);

  await loopBtn.trigger('click');            // all -> one
  note('切到单曲循环', String(loopBtn.dataset.mode) === 'one', String(loopBtn.dataset.mode));
  audioEl.paused = false;

  timers.length = 0;
  audioEl.currentTime = 59;                  // 第一首 0–60
  sandbox.armLoopTimer();
  const t1 = timers.pop();
  if (t1) t1.fn();
  note('单曲循环：第一首到点回到 0:00', audioEl.currentTime === 0, `t=${audioEl.currentTime}`);

  timers.length = 0;
  audioEl.currentTime = 100;                 // 第二首 60–150
  sandbox.armLoopTimer();
  const t2 = timers.pop();
  if (t2) t2.fn();
  note('单曲循环：第二首到点回到 1:00', audioEl.currentTime === 60, `t=${audioEl.currentTime}`);
  await loopBtn.trigger('click');            // one -> off，恢复默认
}
const indexHtml = readFileSync(join(here, '..', 'biliaudio', 'web', 'index.html'), 'utf8');
note('界面已去掉进度条', !indexHtml.includes('progress-bar') && indexHtml.includes('id="job-status"'));

// 解析流程：成功的行自动删除、失败的保留、曲目直接进列表
getEl('#urls').value = 'BV1GJ411x7h7\n不是链接';
await getEl('#btn-parse').trigger('click');
const remaining = String(getEl('#urls').value);
note('解析成功后该行从输入框删除', remaining === '不是链接', JSON.stringify(remaining));
note('解析失败的行保留在输入框', remaining.includes('不是链接'));
const hint = String(getEl('#parse-hint').textContent);
note('状态提示写明新增与失败', hint.includes('新增 1 首') && hint.includes('失败 1 条'), hint);
note('新曲目直接进入列表', String(getEl('#tracks').innerHTML).includes('新歌'));

const failed = checks.filter((c) => !c.ok);
console.log(`\n${failed.length ? `失败 ${failed.length} 项` : '全部通过 ✓'}`);
process.exit(failed.length ? 1 : 0);
