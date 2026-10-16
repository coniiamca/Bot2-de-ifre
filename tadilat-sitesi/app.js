import { LAYERS, STATS, MATS, PROJECTS } from './data.js';

// WhatsApp numaranız (ülke koduyla, yalnız rakam; örn. '905321234567'). Boşken düğme gizlenir.
const WHATSAPP = '';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const smooth = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const pad = (n) => String(n).padStart(2, '0');
const root = document.documentElement;
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const t0 = performance.now();

/* ---------------- Yükleyici sayacı ---------------- */
const mmEl = $('#ld-mm');
(function ldTick() {
  const k = clamp((performance.now() - t0) / 1500);
  const e = 1 - Math.pow(1 - k, 3);
  if (mmEl) mmEl.textContent = String(Math.round(e * 2800)).padStart(4, '0');
  if (k < 1 && !root.classList.contains('loaded')) requestAnimationFrame(ldTick);
})();

let introStart = root.classList.contains('loaded') ? performance.now() : 0;
function finishLoading() {
  const wait = Math.max(0, 1650 - (performance.now() - t0));
  setTimeout(() => {
    root.classList.add('loaded');
    if (!introStart) introStart = performance.now();
    measure();
  }, wait);
}
new MutationObserver(() => { if (root.classList.contains('loaded') && !introStart) introStart = performance.now(); })
  .observe(root, { attributes: true, attributeFilter: ['class'] });

/* ---------------- Yumuşak kaydırma ---------------- */
let lenis = null;
if (window.Lenis && !reduced) {
  try { lenis = new window.Lenis({ lerp: 0.1, touchMultiplier: 1.3 }); } catch (e) { lenis = null; }
}
function scrollToY(y) {
  if (lenis) lenis.scrollTo(y, { duration: 1.5 });
  else window.scrollTo({ top: y, behavior: reduced ? 'auto' : 'smooth' });
}
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href^="#"]');
  if (!a) return;
  const id = a.getAttribute('href').slice(1);
  const el = id ? document.getElementById(id) : document.body;
  if (!el) return;
  e.preventDefault();
  closeMenu();
  scrollToY(id === 'top' ? 0 : el.getBoundingClientRect().top + window.scrollY);
});

/* ---------------- Menü (telefon) ---------------- */
const menu = $('#menu');
const menuBtn = $('#menu-btn');
function closeMenu() { if (!menu.hidden) { menu.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); menuBtn.textContent = 'Menü'; lenis && lenis.start(); } }
menuBtn.addEventListener('click', () => {
  const open = menu.hidden;
  menu.hidden = !open;
  menuBtn.setAttribute('aria-expanded', String(open));
  menuBtn.textContent = open ? 'Kapat' : 'Menü';
  if (lenis) open ? lenis.stop() : lenis.start();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

/* ---------------- Bölümler ve ölçüm ---------------- */
const sections = $$('.sec').map((el, i) => ({
  el, i, scene: el.dataset.scene || null, label: el.dataset.label || '',
  top: 0, h: 0, enter: 0, p: 0, exit: 0, offPx: 0, vis: false,
}));
const byScene = Object.fromEntries(sections.filter((s) => s.scene).map((s) => [s.scene, s]));
let vw = innerWidth, vh = innerHeight;

function measure() {
  vw = innerWidth; vh = innerHeight;
  const y = window.scrollY;
  for (const s of sections) {
    const r = s.el.getBoundingClientRect();
    s.top = r.top + y;
    s.h = s.el.offsetHeight;
  }
  scene && scene.resize();
}
// Animasyonlar kaydırmaya bağlı değil: bölüm ekrana girince kendi süresiyle oynar (saniye).
// s.p  = animasyon ilerlemesi 0→1 (bölüm ekrandan tamamen çıkınca sıfırlanır, dönüşte yeniden oynar)
// s.sp = bölüm içindeki kaydırma ilerlemesi (bölüm göstergesi için)
// s.k  = bölümün ekrandan geçişi 0→1 (modellere hafif bir kaydırma döndürmesi verir)
const AUTO = { hero: 1, manifesto: 1.6, anatomy: 1.6, hidden: 3.2, kitchen: 3.4, bath: 3.4, furniture: 2.6, material: 1 };
function computeSections(y, now) {
  for (const s of sections) {
    s.enter = clamp((y + vh - s.top) / vh);
    s.exit = clamp((y - (s.top + s.h - vh)) / vh);
    s.offPx = (1 - s.enter) * vh - s.exit * vh;
    s.vis = s.enter > 0 && s.exit < 1;
    s.sp = s.h > vh ? clamp((y - s.top) / (s.h - vh)) : clamp((y + vh - s.top) / (s.h + vh));
    s.k = clamp((y + vh - s.top) / (s.h + vh));
    const dur = s.scene && AUTO[s.scene];
    if (!dur) { s.p = s.sp; continue; }
    if (!s.vis) { s.t0 = 0; s.p = 0; continue; }
    if (!s.t0 && s.enter > 0.6) s.t0 = now;
    s.p = !s.t0 ? 0 : reduced ? 1 : clamp((now - s.t0) / 1000 / dur);
  }
}

// Kendiliğinden ilerleyen dizi (katmanlar, testler, malzemeler).
// Süre dolunca sonrakine geçer; elle seçim 8 sn bekletir; Durdur/Oynat düğmesi ve ilerleme çizgisi vardır.
// Hareket azaltma tercihi olanlarda duraklatılmış başlar.
function makeCycle({ n, every, btn, bar, onChange }) {
  const c = { i: 0, t: 0, running: false, paused: reduced, frozen: 0, lastK: -1 };
  const show = (i) => { c.i = ((i % n) + n) % n; onChange(c.i); };
  const setBar = (k) => {
    k = Math.round(clamp(k) * 300) / 300;
    if (k !== c.lastK) { c.lastK = k; bar.style.transform = 'scaleX(' + k + ')'; }
  };
  const label = () => {
    btn.setAttribute('aria-pressed', String(c.paused));
    btn.textContent = c.paused ? 'Oynat' : 'Durdur';
  };
  c.start = (now) => { if (!c.running) { c.running = true; c.t = Math.max(c.t, now); } };
  c.stop = () => { c.running = false; };
  c.reset = () => { c.running = false; c.t = 0; show(0); };
  c.select = (i, now) => { show(i); c.t = now + 8000; c.frozen = 0; };
  c.tick = (now) => {
    let k = 0;
    if (c.paused) k = c.frozen;
    else if (c.running) {
      k = (now - c.t) / every;
      if (k >= 1) { show(c.i + 1); c.t = now; k = 0; }
    }
    setBar(k);
  };
  btn.addEventListener('click', () => {
    const now = performance.now();
    c.paused = !c.paused;
    if (c.paused) c.frozen = clamp((now - c.t) / every);
    else c.t = now - c.frozen * every;
    label();
  });
  label();
  return c;
}

let resizeT;
addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(measure, 120); });
new ResizeObserver(() => measure()).observe(document.body);
document.fonts && document.fonts.ready.then(measure);

/* ---------------- Sayfa renkleri: koyu bölüme girerken ----------------
   Zemin, yalnızca opaklığı değişen sabit bir katmanla kararır (birleştiricide, yeniden boyama yok).
   Yazı rengi her karede değil, zemin yarıyı geçtiğinde tek seferde değişir. */
const bgfx = $('#bgfx');
let lastDw = -1, dw = 0, dark = false;
function applyColors() {
  const d = byScene.hidden;
  dw = smooth(0.4, 0.95, d.enter) * (1 - smooth(0.05, 0.6, d.exit));
  const o = Math.round(dw * 400) / 400;
  if (o !== lastDw) { lastDw = o; bgfx.style.opacity = String(o); }
  const nd = dw > 0.5;
  if (nd !== dark) { dark = nd; root.classList.toggle('dark', dark); }
}

/* ---------------- Bölüm göstergesi + menü bağlantıları ---------------- */
const hudN = $('#hud-n'), hudT = $('#hud-t'), hudBar = $('#hud-bar');
const navLinks = $$('.nav-links a');
let lastHud = -1, lastHp = -1;
function updateHud(y) {
  const mid = y + vh * 0.5;
  let cur = sections[0];
  for (const s of sections) if (mid >= s.top) cur = s;
  const hp = Math.round(cur.sp * 200) / 200;
  if (hp !== lastHp) { lastHp = hp; hudBar.style.transform = 'scaleY(' + hp + ')'; }
  if (cur.i === lastHud) return;
  lastHud = cur.i;
  hudN.textContent = pad(cur.i + 1);
  hudT.textContent = cur.label;
  navLinks.forEach((a) => a.classList.toggle('on', a.getAttribute('href') === '#' + cur.el.id));
}

/* ---------------- Logo: kaydırma hızına tepki veren su terazisi kabarcığı ---------------- */
const bubble = $('#bubble');
let lastY = window.scrollY, vel = 0, lastBx = 99;
function updateBubble(y) {
  const v = lenis ? lenis.velocity : (y - lastY);
  lastY = y;
  vel += (v - vel) * 0.15;
  const bx = Math.round(clamp(-vel * 0.9, -10, 10) * 2) / 2;
  if (bx !== lastBx) { lastBx = bx; bubble.style.transform = 'translateX(' + bx + 'px)'; }
}

/* ---------------- Manifesto: bölüm görününce kelimeler sırayla yanar ---------------- */
const manEl = $('#manifesto-text');
const words = manEl.textContent.trim().split(/\s+/);
manEl.innerHTML = words.map((w) => `<span class="w">${w}</span>`).join(' ');
const wordEls = $$('.w', manEl);
let lastWords = -1;
function updateManifesto() {
  const s = byScene.manifesto;
  if (!s.vis) return;
  const n = Math.round(s.p * wordEls.length);
  if (n === lastWords) return;
  lastWords = n;
  wordEls.forEach((el, i) => el.classList.toggle('on', i < n));
}

/* ---------------- Anatomi: katman etiketleri ve panel ---------------- */
const labelList = $('#ana-labels');
labelList.innerHTML = LAYERS.map((L, i) =>
  `<li><i class="ln" aria-hidden="true"></i><button type="button" aria-label="${L.name}, ${L.mm}"><span class="n">${pad(i + 1)}</span><span class="t">${L.name}</span><span class="mm">${L.mm}</span></button></li>`
).join('');
const labelEls = $$('li', labelList);
const anaN = $('#ana-n'), anaName = $('#ana-name'), anaMm = $('#ana-mm'), anaDesc = $('#ana-desc'), anaStd = $('#ana-std');
let anaIdx = 0;
function showLayer(i) {
  if (i === anaIdx && anaName.textContent === LAYERS[i].name) return;
  anaIdx = i;
  const L = LAYERS[i];
  anaN.textContent = pad(i + 1);
  anaName.textContent = L.name;
  anaMm.textContent = L.mm;
  anaDesc.textContent = L.desc;
  anaStd.textContent = L.std;
  labelEls.forEach((li, j) => li.classList.toggle('on', j === i));
}
// Duvar açıldıktan sonra katmanlar 2,6 sn arayla kendiliğinden gezilir
const anaCycle = makeCycle({ n: LAYERS.length, every: 2600, btn: $('#ana-auto'), bar: $('#ana-bar'), onChange: showLayer });
labelEls.forEach((li, i) => li.querySelector('button').addEventListener('click', () => anaCycle.select(i, performance.now())));
function updateAnatomy(now) {
  const s = byScene.anatomy;
  if (!s.vis) { if (anaCycle.running) anaCycle.reset(); return; }
  if (s.p >= 1) anaCycle.start(now);
  anaCycle.tick(now);
}

/* ---------------- Görünmeyen işçilik: sayaç ---------------- */
const hidNum = $('#hid-num'), hidK = $('#hid-k'), hidD = $('#hid-d'), hidDots = $('#hid-dots');
hidDots.innerHTML = STATS.map((S, i) => `<button type="button" aria-label="${S.key}"${i === 0 ? ' aria-current="true"' : ''}><i></i></button>`).join('');
const dotEls = $$('button', hidDots);
let lastHid = 0;
// Test değerleri 3,4 sn arayla kendiliğinden değişir
const hidCycle = makeCycle({ n: STATS.length, every: 3400, btn: $('#hid-auto'), bar: $('#hid-bar'), onChange: showStat });
dotEls.forEach((b, i) => b.addEventListener('click', () => hidCycle.select(i, performance.now())));
function updateHidden(now) {
  const s = byScene.hidden;
  if (!s.vis) { if (hidCycle.running) hidCycle.reset(); return; }
  if (s.p > 0) hidCycle.start(now);
  hidCycle.tick(now);
}
function showStat(idx) {
  if (idx === lastHid) return;
  lastHid = idx;
  dotEls.forEach((b, i) => (i === idx ? b.setAttribute('aria-current', 'true') : b.removeAttribute('aria-current')));
  const S = STATS[idx];
  hidNum.animate([{ opacity: 0, transform: 'translateY(12%)' }, { opacity: 1, transform: 'none' }], { duration: reduced ? 1 : 600, easing: 'cubic-bezier(.22,.8,.2,1)' });
  hidNum.innerHTML = `${S.num}<small>${S.unit}</small>`;
  hidK.textContent = `${pad(idx + 1)} / ${pad(STATS.length)} · ${S.key}`;
  hidD.textContent = S.desc;
}

/* ---------------- Malzeme kütüphanesi ---------------- */
const matTabs = $$('.mat-nav button');
const matGhost = $('#mat-ghost'), matKind = $('#mat-kind'), matName = $('#mat-name'), matScales = $('#mat-scales'),
  matNote = $('#mat-note'), matI = $('#mat-i'), matUse = $('#mat-use');
let matIdx = 0;
function scalesHtml(M) {
  return M.scales.map(([k, v, d]) =>
    `<li><span>${k}</span><span class="bar" role="img" aria-label="5 üzerinden ${v}">${[1, 2, 3, 4, 5].map((j) => `<i class="${j <= v ? 'f' : ''}"></i>`).join('')}</span><span class="v">${d}</span></li>`
  ).join('');
}
function renderMat(i) {
  const M = MATS[i];
  matTabs.forEach((b, j) => b.setAttribute('aria-selected', String(j === i)));
  matKind.textContent = M.kind;
  matName.textContent = M.name;
  matScales.innerHTML = scalesHtml(M);
  matNote.textContent = M.note;
  matI.textContent = pad(i + 1);
  matUse.textContent = M.use;
  matGhost.classList.add('swap');
  setTimeout(() => {
    matGhost.innerHTML = M.ghost.map((g) => `<span>${g}</span>`).join('');
    matGhost.classList.remove('swap');
  }, reduced ? 0 : 280);
  scene && scene.setMaterial(i);
}
matScales.innerHTML = scalesHtml(MATS[0]);
// Malzemeler 4,5 sn arayla kendiliğinden değişir; sekmeye tıklamak o malzemeyi seçer
const matCycle = makeCycle({ n: MATS.length, every: 4500, btn: $('#mat-auto'), bar: $('#mat-bar'), onChange: (i) => { matIdx = i; renderMat(i); } });
matTabs.forEach((b, i) => b.addEventListener('click', () => matCycle.select(i, performance.now())));
function updateMaterial(now) {
  const s = byScene.material;
  if (!s.vis) { matCycle.stop(); return; }
  if (s.p > 0) matCycle.start(now);
  matCycle.tick(now);
}

/* ---------------- Süreç: tipik takvim ---------------- */
const gantt = $('#gantt'), flowName = $('#flow-name'), flowDur = $('#flow-dur'), flowLead = $('#flow-lead');
const flowTabs = $$('.flow-tabs button');
function renderFlow(key) {
  const P = PROJECTS[key];
  const total = P.weeks * 7;
  flowTabs.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.p === key)));
  flowName.textContent = P.name;
  flowDur.textContent = P.dur;
  flowLead.textContent = P.lead;
  gantt.style.setProperty('--cols', P.weeks);
  const pct = (d) => (d / total) * 100;
  const weeks = Array.from({ length: P.weeks }, (_, i) => `<span style="left:${pct(i * 7 + 3.5)}%">${P.weeks > 8 ? i + 1 : 'H' + (i + 1)}</span>`).join('');
  const rows = P.phases.map(([name, a, b, o = {}], i) => {
    const days = b - a;
    const ms = o.ms != null ? `<i class="g-ms" style="left:${pct(o.ms)}%" title="${o.msLabel}"></i>` : '';
    const lbl = o.ms != null ? `${name} <em class="mute">· ${o.msLabel}</em>` : name;
    return `<div class="g-row"><div class="g-lbl"><b>${lbl}</b><span>${days} gün</span></div>` +
      `<div class="g-track"><i class="g-bar${o.par ? ' par' : ''}" style="left:${pct(a)}%;width:${pct(days)}%;animation-delay:${i * 60}ms"></i>${ms}</div></div>`;
  }).join('');
  gantt.innerHTML =
    `<div class="g-row head"><div class="g-lbl"><span>Aşama</span><span>Süre</span></div><div class="g-weeks">${weeks}</div></div>` +
    rows +
    `<div class="g-row"><div class="g-lbl"><b>Teslim dosyası</b></div><ul class="deliver">${P.deliver.map((d) => `<li>${d}</li>`).join('')}</ul></div>`;
  gantt.querySelectorAll('.g-lbl em').forEach((e) => { e.style.fontStyle = 'normal'; e.style.fontWeight = '400'; });
}
flowTabs.forEach((b) => b.addEventListener('click', () => renderFlow(b.dataset.p)));
renderFlow('banyo');

/* ---------------- Keşif formu ---------------- */
const form = $('#lead-form'), formErr = $('#form-err'), formOk = $('#form-ok'), summary = $('#form-summary');
const copyBtn = $('#copy-btn'), waBtn = $('#wa-btn');
form.addEventListener('submit', (e) => {
  e.preventDefault();
  const fd = new FormData(form);
  const types = fd.getAll('tur');
  const ad = (fd.get('ad') || '').toString().trim();
  const tel = (fd.get('tel') || '').toString().trim();
  const digits = tel.replace(/\D/g, '');
  const errs = [];
  if (!types.length) errs.push('en az bir proje türü seçin');
  if (!ad) errs.push('adınızı yazın');
  if (digits.length < 10) errs.push('telefon numaranızı alan koduyla yazın');
  if (errs.length) {
    formErr.textContent = 'Göndermeden önce ' + errs.join(', ') + '.';
    formErr.hidden = false;
    formOk.hidden = true;
    return;
  }
  formErr.hidden = true;
  const m2 = (fd.get('m2') || '').toString().trim();
  const ilce = (fd.get('ilce') || '').toString().trim();
  const not = (fd.get('not') || '').toString().trim();
  const lines = [
    'Keşif talebi · A—Z Tadilat',
    'Proje: ' + types.join(', '),
    m2 ? 'Alan: ' + m2 + ' m²' : null,
    ilce ? 'Konum: ' + ilce : null,
    'Ad: ' + ad,
    'Telefon: ' + tel,
    not ? 'Not: ' + not : null,
  ].filter(Boolean);
  const text = lines.join('\n');
  summary.textContent = text;
  if (WHATSAPP) {
    waBtn.href = `https://wa.me/${WHATSAPP}?text=${encodeURIComponent(text)}`;
    waBtn.hidden = false;
  }
  formOk.hidden = false;
});
copyBtn.addEventListener('click', () => {
  const text = summary.textContent;
  const done = () => { copyBtn.textContent = 'Kopyalandı'; setTimeout(() => { copyBtn.textContent = 'Metni kopyala'; }, 1800); };
  const fallback = () => {
    const r = document.createRange(); r.selectNodeContents(summary);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    copyBtn.textContent = 'Metin seçildi, kopyalayın';
  };
  try { navigator.clipboard.writeText(text).then(done, fallback); } catch (e) { fallback(); }
});

/* ---------------- İmleç ---------------- */
const pointer = { x: 0, y: 0 };
addEventListener('pointermove', (e) => { pointer.x = (e.clientX / vw) * 2 - 1; pointer.y = (e.clientY / vh) * 2 - 1; }, { passive: true });

/* ---------------- 3D sahne ---------------- */
let scene = null;
measure();
import('./scene.js')
  .then((m) => m.createScene({
    canvas: $('#gl'),
    sections: byScene,
    layers: LAYERS,
    mats: MATS,
    labelEls,
    pinRoots: { kitchen: $('#pins-kitchen'), bath: $('#pins-bath'), furniture: $('#pins-furniture') },
    reduced,
  }))
  .then((s) => { scene = s; measure(); scene.setMaterial(matIdx); finishLoading(); })
  .catch((err) => { console.warn('3D sahne yüklenemedi:', err); root.classList.add('no-gl'); finishLoading(); });

/* ---------------- Ana döngü ---------------- */
function tick(time) {
  lenis && lenis.raf(time);
  const y = window.scrollY;
  const now = performance.now();
  computeSections(y, now);
  applyColors();
  updateHud(y);
  updateBubble(y);
  updateManifesto();
  updateAnatomy(now);
  updateHidden(now);
  updateMaterial(now);
  if (scene) {
    const intro = introStart ? (performance.now() - introStart) / 1000 : -1;
    scene.update(time / 1000, { pointer, vw, vh, anaIdx, matIdx, dw, intro });
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
