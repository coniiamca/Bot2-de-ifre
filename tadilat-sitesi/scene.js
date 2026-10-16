import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const range = (v, a, b) => clamp((v - a) / (b - a));
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const fmt = (n, d = 2) => n.toFixed(d).replace('.', ',');

const INK = 0x1a1a19;
const BRASS = 0xB08A55;

/* ------------------------------------------------------------------ */
/* Prosedürel dokular                                                  */
/* paintTexture kendi içinde bağımsızdır ve bir Web Worker'da çalışır; */
/* doku üretimi kaydırma sırasında ana iş parçacığını hiç durdurmaz.   */
/* ------------------------------------------------------------------ */
function paintTexture(kind, size) {
  let s = 11 + kind.length * 101;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const perm = [];
  for (let i = 0; i < 256; i++) perm.push(i);
  for (let i = 255; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); const t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
  const p = new Uint8Array(512);
  for (let i = 0; i < 512; i++) p[i] = perm[i & 255];
  const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
  const lp = (a, b, t) => a + (b - a) * t;
  const cl = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
  const grad = (h, x, y) => {
    switch (h & 7) {
      case 0: return x + y; case 1: return -x + y; case 2: return x - y; case 3: return -x - y;
      case 4: return x; case 5: return -x; case 6: return y; default: return -y;
    }
  };
  const noise = (x, y) => {
    const xi = Math.floor(x), yi = Math.floor(y);
    const X = xi & 255, Y = yi & 255;
    x -= xi; y -= yi;
    const u = fade(x), v = fade(y);
    const a = p[X] + Y, b = p[X + 1] + Y;
    return lp(lp(grad(p[a], x, y), grad(p[b], x - 1, y), u), lp(grad(p[a + 1], x, y - 1), grad(p[b + 1], x - 1, y - 1), u), v);
  };
  const fbm = (x, y, o) => {
    let f = 0, a = 0.5;
    for (let i = 0; i < o; i++) { f += a * noise(x, y); x = x * 2.03 + 17.1; y = y * 2.03 + 3.7; a *= 0.5; }
    return f;
  };
  const out = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = x / size, v = y / size, i = (y * size + x) * 4;
      let r, g, b;
      if (kind === 'marble') {
        const n = fbm(u * 2.6, v * 2.6, 5), q = fbm(u * 1.3 + 4.1, v * 1.3 + 9.2, 3);
        const vein = Math.pow(1 - Math.abs(Math.sin((u * 1.4 + v * 0.9 + n * 1.9 + q * 1.4) * Math.PI * 1.7)), 26);
        const fine = Math.pow(1 - Math.abs(Math.sin((u * 3.2 - v * 2.1 + n * 3.4) * Math.PI)), 60) * 0.55;
        const c = 0.965 + 0.035 * n;
        r = 246 * c; g = 244 * c; b = 240 * c;
        let k = cl(vein * 0.55 + fine * 0.25); r = lp(r, 182, k); g = lp(g, 160, k); b = lp(b, 120, k);
        k = cl(vein * 0.7 * (0.6 + q)); r = lp(r, 96, k); g = lp(g, 92, k); b = lp(b, 86, k);
      } else if (kind === 'porcelain') {
        const n = fbm(u * 2 + 30, v * 2 + 11, 5);
        const vein = Math.pow(1 - Math.abs(Math.sin((u * 1.1 - v * 1.6 + n * 2.6) * Math.PI * 1.3)), 34);
        const c = 0.92 + 0.12 * fbm(u * 6, v * 6, 3);
        const k = cl(vein * 0.8);
        r = lp(46 * c, 168, k); g = lp(46 * c, 162, k); b = lp(45 * c, 152, k);
      } else if (kind === 'oak') {
        const warp = fbm(u * 1.2, v * 3, 3) * 1.4;
        const ring = (((v * 9 + warp + fbm(u * 0.5 + 3, v * 1.5, 2) * 2) % 1) + 1) % 1;
        const k = Math.pow(Math.abs(Math.sin(ring * Math.PI)), 0.55) * 0.7 + (noise(u * 3, v * 300) + 1) * 0.15;
        r = lp(140, 204, k); g = lp(100, 164, k); b = lp(64, 118, k);
      } else if (kind === 'quartz') {
        const n = 233 + fbm(u * 9, v * 9, 3) * 10;
        r = n; g = n - 1; b = n - 4;
      } else { // plaster
        const n = 214 + fbm(u * 14, v * 14, 4) * 18 + (rnd() - 0.5) * 10;
        r = n + 3; g = n; b = n - 5;
      }
      out[i] = r; out[i + 1] = g; out[i + 2] = b; out[i + 3] = 255;
    }
  }
  if (kind === 'quartz') {
    const dots = Math.round(9000 * (size / 512) * (size / 512));
    for (let d = 0; d < dots; d++) {
      const cx = rnd() * size, cy = rnd() * size, rr = rnd() * 1.6 + 0.3, gr = 120 + rnd() * 90, al = 0.35 + rnd() * 0.5;
      for (let y = Math.floor(cy - rr); y <= cy + rr; y++) {
        for (let x = Math.floor(cx - rr); x <= cx + rr; x++) {
          if ((x - cx) * (x - cx) + (y - cy) * (y - cy) > rr * rr) continue;
          const i = ((((y % size) + size) % size) * size + (((x % size) + size) % size)) * 4;
          out[i] = lp(out[i], gr, al); out[i + 1] = lp(out[i + 1], gr - 2, al); out[i + 2] = lp(out[i + 2], gr - 6, al);
        }
      }
    }
  }
  return new Uint8Array(out.buffer);
}

// İki Worker'a kadar paralel üretim; Worker açılamazsa ana iş parçacığında sırayla üretir.
function makeTexturer() {
  const workers = [];
  const jobs = new Map();
  let id = 0, rr = 0;
  const fallback = (job) => setTimeout(() => job.resolve(paintTexture(job.k, job.size)), 0);
  try {
    const src = paintTexture.toString() + '\nself.onmessage=function(e){var d=e.data;var b=paintTexture(d.k,d.size);self.postMessage({id:d.id,buf:b},[b.buffer]);};';
    const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    const n = Math.min(2, Math.max(1, (navigator.hardwareConcurrency || 2) - 1));
    for (let i = 0; i < n; i++) {
      const w = new Worker(url);
      w.onmessage = (e) => { const job = jobs.get(e.data.id); jobs.delete(e.data.id); job && job.resolve(e.data.buf); };
      w.onerror = () => {
        workers.splice(workers.indexOf(w), 1);
        for (const [jid, job] of jobs) if (job.w === w) { jobs.delete(jid); fallback(job); }
      };
      workers.push(w);
    }
  } catch (e) { workers.length = 0; }
  return (k, size) => new Promise((resolve) => {
    const job = { k, size, resolve };
    if (!workers.length) return fallback(job);
    job.w = workers[rr++ % workers.length];
    jobs.set(++id, job);
    job.w.postMessage({ id, k, size });
  });
}

const rnd = (() => { let s = 11; return () => ((s = (s * 16807) % 2147483647) / 2147483647); })();

function canvasTex(renderer, size, paint, { srgb = true, w = size, h = size } = {}) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  paint(ctx, w, h);
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}
function dataTex(buf, size) {
  const t = new THREE.DataTexture(buf, size, size, THREE.RGBAFormat, THREE.UnsignedByteType);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.magFilter = THREE.LinearFilter;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  t.generateMipmaps = true;
  t.needsUpdate = true;
  return t;
}
// Gerçek doku gelene kadar aynı tipte 1×1 yer tutucu: değişince gölgelendirici yeniden derlenmez
const placeholderTex = (rgb) => dataTex(new Uint8Array([rgb[0], rgb[1], rgb[2], 255]), 1);

const brickPaint = (ctx, w, h) => {
  ctx.fillStyle = '#c4bcb0'; ctx.fillRect(0, 0, w, h);
  const rows = 12, cols = 6, bh = h / rows, bw = w / cols;
  for (let r = 0; r < rows; r++) {
    const off = (r % 2) * bw / 2;
    for (let c = -1; c <= cols; c++) {
      const v = rnd() * 20 - 10;
      ctx.fillStyle = `rgb(${172 + v},${128 + v * 0.8},${101 + v * 0.6})`;
      ctx.fillRect(c * bw + off + 3, r * bh + 3, bw - 6, bh - 6);
    }
  }
};

/* ------------------------------------------------------------------ */
/* Yardımcılar                                                          */
/* ------------------------------------------------------------------ */
const std = (color, rough = 0.85, metal = 0, env = 0.45) =>
  new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal, envMapIntensity: env });
const brassMat = () => new THREE.MeshStandardMaterial({ color: 0xC9A267, metalness: 1, roughness: 0.3, envMapIntensity: 1.1 });
const edgeMat = (color = INK, opacity = 0.24) => new THREE.LineBasicMaterial({ color, transparent: true, opacity });

function maker(parent, edge) {
  const fn = (geo, mat, x = 0, y = 0, z = 0, o = {}) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    if (o.rx || o.ry || o.rz) m.rotation.set(o.rx || 0, o.ry || 0, o.rz || 0);
    if (o.edges !== false && (o.edge || edge)) {
      const l = new THREE.LineSegments(new THREE.EdgesGeometry(geo, o.angle || 25), o.edge || edge);
      m.add(l);
      m.userData.edge = l;
    }
    (o.parent || parent).add(m);
    return m;
  };
  // tabana hizalı kutu: y0 = alt yüz
  fn.box = (w, h, d, mat, x, y0, z, o) => fn(new THREE.BoxGeometry(w, h, d), mat, x, y0 + h / 2, z, o);
  return fn;
}

function roundedPath(pts, r = 0.08) {
  const P = pts.map((p) => new THREE.Vector3(...p));
  const path = new THREE.CurvePath();
  let prev = P[0].clone();
  for (let i = 1; i < P.length - 1; i++) {
    const a = P[i - 1], b = P[i], c = P[i + 1];
    const d1 = b.clone().sub(a); const l1 = d1.length(); d1.normalize();
    const d2 = c.clone().sub(b); const l2 = d2.length(); d2.normalize();
    const rr = Math.min(r, l1 / 2, l2 / 2);
    const p1 = b.clone().addScaledVector(d1, -rr);
    const p2 = b.clone().addScaledVector(d2, rr);
    if (prev.distanceTo(p1) > 1e-4) path.add(new THREE.LineCurve3(prev.clone(), p1));
    path.add(new THREE.QuadraticBezierCurve3(p1, b.clone(), p2));
    prev = p2;
  }
  path.add(new THREE.LineCurve3(prev, P[P.length - 1]));
  return path;
}
function tube(path, radius, mat, radial = 10) {
  const len = path.getLength();
  const seg = Math.max(8, Math.round(len * 70));
  const geo = new THREE.TubeGeometry(path, seg, radius, radial, false);
  const m = new THREE.Mesh(geo, mat);
  m.userData.seg = seg; m.userData.radial = radial;
  m.userData.draw = (k) => { geo.setDrawRange(0, Math.floor(clamp(k) * seg) * radial * 6); m.visible = k > 0.001; };
  return m;
}

// Hareket etmeyen parçaları malzemeye göre tek geometride birleştirir: yüzlerce çizim çağrısı yerine birkaç tane.
function bake(parent, skip = new Set()) {
  const meshes = new Map(), lines = new Map(), dead = [];
  const push = (map, mat, g) => { if (!map.has(mat)) map.set(mat, []); map.get(mat).push(g); };
  const strip = (geo, withNormal, withUv, matrix) => {
    const src = geo.index ? geo.toNonIndexed() : geo;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', src.getAttribute('position').clone());
    if (withNormal) g.setAttribute('normal', src.getAttribute('normal').clone());
    if (withUv) g.setAttribute('uv', src.getAttribute('uv').clone());
    g.applyMatrix4(matrix);
    return g;
  };
  for (const o of parent.children) {
    if (skip.has(o) || !(o.isMesh || o.isLineSegments) || Array.isArray(o.material)) continue;
    if (o.children.some((c) => !c.isLineSegments)) continue;
    o.updateMatrix();
    if (o.isMesh) {
      push(meshes, o.material, strip(o.geometry, true, !!o.material.map, o.matrix));
      for (const e of o.children) {
        e.updateMatrix();
        push(lines, e.material, strip(e.geometry, false, false, new THREE.Matrix4().multiplyMatrices(o.matrix, e.matrix)));
      }
    } else {
      push(lines, o.material, strip(o.geometry, false, false, o.matrix));
    }
    dead.push(o);
  }
  dead.forEach((o) => parent.remove(o));
  const out = { meshes: [], lines: [] };
  const add = (obj, list) => { obj.matrixAutoUpdate = false; parent.add(obj); list.push(obj); };
  for (const [mat, list] of meshes) add(new THREE.Mesh(mergeGeometries(list), mat), out.meshes);
  for (const [mat, list] of lines) add(new THREE.LineSegments(mergeGeometries(list), mat), out.lines);
  return out;
}

/* ------------------------------------------------------------------ */
export async function createScene({ canvas, sections, layers, mats, labelEls, pinRoots, reduced }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  if (!renderer.getContext()) throw new Error('WebGL yok');
  // Çözünürlük üst sınırı 1,5; kareler yavaşlarsa update() içinde kademeli düşer
  let dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  renderer.setPixelRatio(dpr);
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  // Beyaz mimari maket ışığı: gökyüzü + önden yumuşak dolgu, üst yüzler beyaz, düşey yüzler açık gri
  scene.add(new THREE.HemisphereLight(0xffffff, 0xbdbab4, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.5); key.position.set(2, 5, 7); scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.9); fill.position.set(-5, 1, 8); scene.add(fill);

  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 120);
  camera.position.set(0, 0, 14);
  const isSmall = Math.min(window.innerWidth, window.innerHeight) < 700;

  let vw = 0, vh = 0, aspect = 1, H = 1, W = 1;
  let plasterMat = null;
  const ptr = { x: 0, y: 0 };
  const groups = [];
  const pins = [];

  function addGroup(name, build) {
    const root = new THREE.Group();
    const pivot = new THREE.Group();
    root.add(pivot);
    scene.add(root);
    const g = Object.assign({ name, root, pivot, sec: sections[name] }, build(pivot));
    groups.push(g);
    return g;
  }
  function addPin(rootEl, text, obj, local, opts = {}) {
    const el = document.createElement('div');
    el.className = 'pin off' + (opts.tag ? ' tag' : '');
    el.textContent = text;
    rootEl.appendChild(el);
    const pin = { el, obj, local: new THREE.Vector3(...local), sec: opts.sec, show: opts.show || (() => true), tag: !!opts.tag, on: false };
    pins.push(pin);
    return pin;
  }

  /* ================= 01 · Giriş: örnek daire maketi ================= */
  addGroup('hero', (pivot) => {
    const M = {
      white: std(0xF5F4F1, 0.86), floor: std(0xE8E6E2, 0.92), cut: std(0x1D1D1C, 0.7, 0, 0.3),
      dark: std(0x2C2C2B, 0.4, 0.2, 0.6), brass: brassMat(), tile: std(0xEDECE9, 0.5), rug: std(0xD9D6D0, 1),
      glass: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.05, metalness: 0.1, transparent: true, opacity: 0.2, depthWrite: false }),
      mirror: new THREE.MeshStandardMaterial({ color: 0xdfe2e4, roughness: 0.05, metalness: 1 }),
    };
    const E = edgeMat(INK, 0.24);
    const P = maker(pivot, E);
    const pieces = [];
    let order = 0, k = 0;
    const add = (m) => { pieces.push({ m, y: m.position.y, order, k: k++ }); return m; };
    const B = (...a) => add(P.box(...a));
    const step = (o) => { order = o; k = 0; };

    step(0);
    B(6.3, 0.2, 5.1, M.floor, 0, -0.2, 0);
    step(1);
    B(6.3, 2.8, 0.12, M.white, 0, 0, -2.49);
    B(0.12, 2.8, 1.05, M.white, -3.09, 0, -2.025);
    B(0.12, 0.95, 1.2, M.white, -3.09, 0, -0.9);
    B(0.12, 0.5, 1.2, M.white, -3.09, 2.3, -0.9);
    B(0.12, 2.8, 2.85, M.white, -3.09, 0, 1.125);
    B(0.03, 1.35, 1.2, M.glass, -3.09, 0.95, -0.9, { edges: false });
    B(0.05, 1.35, 0.035, M.dark, -3.09, 0.95, -0.9, { edges: false });
    B(0.1, 1.3, 1.18, M.white, 1.16, 0, -1.84);
    B(0.1, 0.014, 1.18, M.cut, 1.16, 1.3, -1.84, { edges: false });
    B(2.04, 1.3, 0.1, M.white, 2.13, 0, -0.4);
    B(2.04, 0.014, 0.1, M.cut, 2.13, 1.3, -0.4, { edges: false });

    step(2); // mutfak
    B(0.62, 1.75, 0.6, M.white, -2.72, 0, -2.13);
    B(0.62, 0.5, 0.6, M.white, -2.72, 1.75, -2.13);
    B(3.11, 0.1, 0.52, M.dark, -0.855, 0, -2.17, { edges: false });
    for (const [a, b] of [[-2.41, -1.81], [-1.81, -1.21], [-1.21, -0.41], [-0.41, 0.19], [0.19, 0.7]]) B(b - a - 0.006, 0.78, 0.58, M.white, (a + b) / 2, 0.1, -2.14);
    B(3.13, 0.04, 0.63, M.white, -0.855, 0.88, -2.125);
    B(0.5, 0.01, 0.38, M.dark, -0.11, 0.917, -2.12, { edges: false });
    B(0.6, 0.01, 0.5, M.dark, -0.81, 0.918, -2.12, { edges: false });
    for (const [a, b] of [[-2.41, -1.81], [-1.81, -1.21], [-0.41, 0.19], [0.19, 0.7]]) B(b - a - 0.006, 0.72, 0.35, M.white, (a + b) / 2, 1.48, -2.255);
    B(0.78, 0.22, 0.46, M.white, -0.81, 1.62, -2.23);
    B(0.3, 0.96, 0.26, M.white, -0.81, 1.84, -2.3);
    add(P(new THREE.CylinderGeometry(0.012, 0.012, 0.28, 12), M.brass, -0.11, 1.06, -2.36, { edges: false }));
    add(P(new THREE.CylinderGeometry(0.01, 0.01, 0.18, 12), M.brass, -0.11, 1.19, -2.27, { edges: false, rx: Math.PI / 2 }));

    step(3); // ada
    for (const s of [-1, 0, 1]) B(0.694, 0.88, 0.9, M.white, -0.95 + s * 0.7, 0, -0.6);
    B(2.3, 0.04, 1.08, M.white, -0.95, 0.88, -0.48);
    for (const s of [-1, 0, 1]) {
      const x = -0.95 + s * 0.68;
      add(P(new THREE.CylinderGeometry(0.17, 0.17, 0.05, 28), M.dark, x, 0.66, 0.34, { angle: 40 }));
      add(P(new THREE.CylinderGeometry(0.014, 0.014, 0.64, 8), M.brass, x, 0.32, 0.34, { edges: false }));
      add(P(new THREE.CylinderGeometry(0.003, 0.003, 1.05, 4), M.dark, x, 2.28, -0.5, { edges: false }));
      add(P(new THREE.ConeGeometry(0.13, 0.17, 28, 1, true), M.brass, x, 1.7, -0.5, { edges: false }));
    }

    step(4); // banyo
    B(1.93, 0.02, 1.97, M.tile, 2.155, 0, -1.43, { edges: false });
    {
      const pts = [];
      for (let x = 1.25; x < 3.1; x += 0.3) pts.push(x, 0.022, -2.42, x, 0.022, -0.45);
      for (let z = -2.4; z < -0.45; z += 0.3) pts.push(1.2, 0.022, z, 3.12, 0.022, z);
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      const ls = new THREE.LineSegments(g, edgeMat(INK, 0.12));
      pivot.add(ls);
      pieces.push({ m: ls, y: 0, order, k: k++ });
    }
    B(0.012, 1.98, 0.88, M.glass, 2.25, 0.02, -1.99, { edge: edgeMat(INK, 0.35) });
    B(0.6, 0.006, 0.06, M.dark, 2.7, 0.022, -1.62, { edges: false });
    add(P(new THREE.CylinderGeometry(0.09, 0.09, 0.015, 24), M.brass, 2.7, 2.0, -2.25, { edges: false }));
    add(P(new THREE.CylinderGeometry(0.008, 0.008, 0.26, 8), M.brass, 2.7, 2.0, -2.38, { edges: false, rx: Math.PI / 2 }));
    add(P(new THREE.BoxGeometry(0.12, 0.12, 0.04), M.brass, 2.7, 1.1, -2.41, { edges: false }));
    B(0.65, 0.4, 0.46, M.white, 1.62, 0.45, -2.2);
    B(0.67, 0.05, 0.48, M.white, 1.62, 0.85, -2.2);
    B(0.38, 0.012, 0.28, M.dark, 1.62, 0.9, -2.17, { edges: false });
    B(0.6, 0.75, 0.015, M.mirror, 1.62, 1.15, -2.425, { edges: false });
    add(P(new RoundedBoxGeometry(0.36, 0.34, 0.54, 4, 0.1), M.white, 2.78, 0.42, -0.74, { angle: 50 }));

    step(5); // mobilya
    B(0.56, 0.08, 1.8, M.dark, -2.75, 0, 1.5, { edges: false });
    for (const cz of [0.9, 1.5, 2.1]) {
      B(0.6, 2.3, 0.596, M.white, -2.73, 0.08, cz);
      B(0.012, 0.5, 0.012, M.brass, -2.424, 1.0, cz - 0.25, { edges: false });
    }
    B(2.6, 0.012, 1.7, M.rug, -0.3, 0, 1.35, { edges: false });
    B(2.0, 0.42, 0.9, M.white, -0.3, 0, 1.95);
    B(2.0, 0.4, 0.18, M.white, -0.3, 0.42, 2.31);
    B(0.18, 0.2, 0.9, M.white, -1.21, 0.42, 1.95);
    B(0.18, 0.2, 0.9, M.white, 0.61, 0.42, 1.95);
    add(P(new THREE.CylinderGeometry(0.34, 0.34, 0.04, 40), M.white, -0.3, 0.38, 0.98, { angle: 40 }));
    add(P(new THREE.CylinderGeometry(0.03, 0.03, 0.36, 10), M.dark, -0.3, 0.18, 0.98, { edges: false }));
    add(P(new THREE.CylinderGeometry(0.01, 0.01, 1.5, 8), M.brass, 1.15, 0.75, 2.25, { edges: false }));
    add(P(new THREE.ConeGeometry(0.16, 0.2, 28, 1, true), M.brass, 1.15, 1.55, 2.25, { edges: false }));

    // Kurulum animasyonu bitince ~200 parça yerine birleştirilmiş kopya çizilir
    const baked = new THREE.Group();
    for (const pc of pieces) baked.add(pc.m.clone());
    bake(baked);
    baked.visible = false;
    pivot.add(baked);
    let introDone = false;
    return {
      layout: () => (aspect < 0.85
        ? { x: W * 0.04, y: 0.75, s: Math.min(0.7, (W * 0.8) / 6.6) }
        : { x: W * 0.16, y: 0.55, s: Math.min(0.62, (W * 0.4) / 6.6, (H * 0.56) / 5.2) }),
      update(t, s, ctx) {
        if (!introDone) {
          const it = ctx.intro;
          let all = true;
          for (const pc of pieces) {
            if (it < 0) { pc.m.visible = false; all = false; continue; }
            const d = pc.order * 0.22 + pc.k * 0.022;
            const e = reduced ? 1 : easeOut(clamp((it - d) / 0.8));
            pc.m.visible = e > 0;
            pc.m.position.y = pc.y + (1 - e) * 1.8;
            if (e < 1) all = false;
          }
          if (all) {
            introDone = true;
            for (const pc of pieces) pc.m.visible = false;
            baked.visible = true;
          }
        }
        const k = s.k - 0.5; // kaydırmanın verdiği hafif döndürme
        pivot.rotation.y = -0.74 + (reduced ? 0 : Math.sin(t * 0.22) * 0.05) + k * 0.6 + ptr.x * 0.07;
        pivot.rotation.x = 0.5 + k * 0.1 + ptr.y * 0.03;
      },
    };
  });

  /* ================= 03 · Anatomi: patlatılmış duvar kesiti ================= */
  const ana = addGroup('anatomy', (pivot) => {
    const HH = 2.4, D = 1.8;
    const edge = edgeMat(INK, 0.3);
    const edgeHi = new THREE.LineBasicMaterial({ color: BRASS, transparent: true, opacity: 1 });
    const plates = [];
    layers.forEach((L) => {
      const g = new THREE.Group();
      pivot.add(g);
      let mat;
      if (L.tex === 'brick') mat = new THREE.MeshStandardMaterial({ map: canvasTex(renderer, 512, brickPaint), roughness: 0.95, envMapIntensity: 0.6 });
      else if (L.tex === 'plaster') mat = plasterMat = new THREE.MeshStandardMaterial({ map: placeholderTex([214, 211, 205]), roughness: 1, envMapIntensity: 0.7 });
      else if (L.tiles) mat = new THREE.MeshPhysicalMaterial({ color: L.color, roughness: 0.18, clearcoat: 0.6, envMapIntensity: 0.8 });
      else mat = std(L.color, 0.75, 0, 0.7);
      const meshes = [];
      const box = (w, h, d, x = 0, y = 0, z = 0, m = mat, rz = 0) => {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
        mesh.position.set(x, y, z); mesh.rotation.z = rz;
        g.add(mesh); meshes.push(mesh); return mesh;
      };
      if (L.tiles) {
        for (let r = 0; r < 2; r++) for (let c = 0; c < 3; c++) box(L.t, 1.2 - 0.016, 0.6 - 0.016, 0, -0.6 + r * 1.2, -0.6 + c * 0.6);
      } else if (L.grout) {
        for (const z of [-0.3, 0.3]) box(L.t, HH, 0.016, 0, 0, z);
        box(L.t, 0.016, D, 0, 0, 0);
      } else if (L.band) {
        box(L.t, 0.14, D, 0, -HH / 2 + 0.07, 0);
        box(L.t, HH - 0.14, 0.14, 0, 0.07, -D / 2 + 0.07);
      } else if (L.coats) {
        box(L.t * 0.42, HH, D, -L.t * 0.29);
        box(L.t * 0.42, HH, D, L.t * 0.29, 0, 0, std(0x6F7F8C, 0.7, 0, 0.7));
      } else if (L.ridges) {
        box(L.t * 0.4, HH, D, -L.t * 0.3);
        for (let i = 0; i < 13; i++) box(0.034, 0.034, D, -L.t * 0.08, -HH / 2 + 0.1 + i * 0.183, 0, mat, Math.PI / 4);
      } else {
        box(L.t, HH, D);
      }
      meshes.forEach((m) => m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry, 25), edge)));
      const baked = bake(g);
      plates.push({ g, L, lines: baked.lines, x0: 0, x1: 0, lift: 0 });
    });
    // Görünmeyen algılama kutuları: ince katmanlar (astar, derz) da imleçle kolayca yakalanır
    const hitMat = new THREE.MeshBasicMaterial();
    // Köşe bandı ve derz seyrek şekiller: algılama alanı da şeritler/çubuklar kadar, arkadaki katmanı örtmez
    plates.forEach((pl, i) => {
      const t = Math.max(pl.L.t * 1.3, 0.16);
      const boxes = pl.L.band ? [[t, 0.24, D, 0, -HH / 2 + 0.12, 0], [t, HH, 0.24, 0, 0, -D / 2 + 0.12]]
        : pl.L.grout ? [[t, HH, 0.12, 0, 0, -0.3], [t, HH, 0.12, 0, 0, 0.3], [t, 0.12, D, 0, 0, 0]]
        : [[t, HH, D, 0, 0, 0]];
      pl.hits = boxes.map(([w, h, d, x, y, z]) => {
        const hb = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), hitMat);
        hb.position.set(x, y, z);
        hb.visible = false;
        hb.userData.plate = i;
        pl.g.add(hb);
        return hb;
      });
    });
    const hitList = () => plates.flatMap((pl) => pl.hits);
    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let acc = 0;
    plates.forEach((pl) => { pl.x0 = acc + pl.L.t / 2; acc += pl.L.t; });
    const gap = 0.5;
    plates.forEach((pl, i) => { pl.x1 = pl.x0 + i * gap; });
    const mid0 = acc / 2;
    const mid1 = (plates[0].x1 + plates[plates.length - 1].x1) / 2;
    const anchor = new THREE.Vector3();
    const tmp = new THREE.Vector3();
    let frameEl = labelEls[0] && labelEls[0].closest('.frame');
    let bh = [], fh = 0;
    const lastKey = [];
    const lnEls = labelEls.map((li) => li.querySelector('.ln'));

    return {
      plates,
      layout: () => (aspect < 0.85
        ? { x: 0, y: 0.3, s: Math.min(0.8, (W * 0.9) / 4.6) }
        : { x: 0, y: -0.3, s: Math.min(1.0, (W * 0.48) / 4.6, (H * 0.46) / 2.6) }),
      update(t, s, ctx) {
        const p = s.p;
        const e = easeInOut(p);
        const stepping = p >= 1;
        plates.forEach((pl, i) => {
          const on = stepping && i === ctx.anaIdx;
          pl.lift = lerp(pl.lift, on ? 0.14 : 0, 0.14);
          pl.g.position.set(lerp(pl.x0 - mid0, pl.x1 - mid1, e), pl.lift, 0);
          const em = on ? edgeHi : edge;
          for (const l of pl.lines) if (l.material !== em) l.material = em;
        });
        pivot.rotation.y = lerp(-1.3, -0.84, easeOut(p)) + (s.k - 0.5) * 0.3 + ptr.x * 0.05;
        pivot.rotation.x = 0.17 + ptr.y * 0.03;
      },
      invalidate() { fh = 0; bh = []; lastKey.length = 0; },
      // Ekran koordinatındaki katman (duvar açılmış değilse -1)
      pick(cx, cy, p) {
        if (p < 0.95) return -1;
        ndc.set((cx / vw) * 2 - 1, -(cy / vh) * 2 + 1);
        ray.setFromCamera(ndc, camera);
        const hits = ray.intersectObjects(hitList(), false);
        return hits.length ? hits[0].object.userData.plate : -1;
      },
      // 3D sahne güncellendikten sonra: alttaki dikey etiketleri katmanların altına hizala.
      // Yerleşim yalnızca boyut değişince okunur; DOM'a yalnızca değer değişince yazılır.
      place(s) {
        if (!labelEls.length) return;
        if (!frameEl) frameEl = labelEls[0].closest('.frame');
        if (!fh) fh = frameEl ? frameEl.clientHeight : vh;
        if (bh.length !== labelEls.length || bh.some((v) => !v)) bh = labelEls.map((li) => li.querySelector('button').offsetHeight);
        const safe = 26;
        const op = range(s.p, 0.35, 0.9).toFixed(2);
        plates.forEach((pl, i) => {
          const li = labelEls[i];
          anchor.set(pl.L.t / 2, -1.2, 0.9);
          pl.g.localToWorld(tmp.copy(anchor));
          tmp.project(camera);
          const x = Math.round((tmp.x * 0.5 + 0.5) * vw);
          const y = (-tmp.y * 0.5 + 0.5) * vh - s.offPx;
          const lh = Math.max(0, Math.round(fh - (safe + bh[i] + 10) - (y + 10)));
          const key = x + '|' + lh + '|' + op;
          if (lastKey[i] === key) return;
          lastKey[i] = key;
          li.style.transform = 'translateX(' + x + 'px)';
          li.style.opacity = op;
          if (lnEls[i]) {
            lnEls[i].style.bottom = (safe + bh[i] + 10) + 'px';
            lnEls[i].style.transform = 'scaleY(' + (lh / 1000).toFixed(4) + ')';
          }
        });
      },
    };
  });

  /* ================= 04 · Görünmeyen: tesisat röntgeni ================= */
  // Parçacık alanı: kaydırmayla değil, koyu zeminle birlikte görünür.
  // Hareket tamamen GPU'da hesaplanır (köşe gölgelendiricisi): her karede CPU döngüsü ve tampon yüklemesi yok.
  const particles = (() => {
    const ribbons = 7;
    const per = isSmall ? 380 : 950;
    const count = ribbons * per;
    const rot = [];
    const params = [];
    const e = new THREE.Euler(), m4 = new THREE.Matrix4();
    for (let r = 0; r < ribbons; r++) {
      params.push([3.2 + r * 0.55 + rnd() * 0.6, 1.2 + rnd() * 1.4, rnd() * 6.28, rnd() * 6.28]);
      e.set((rnd() - 0.5) * 1.3, (rnd() - 0.5) * 1.0, (rnd() - 0.5) * 1.6);
      rot.push(new THREE.Matrix3().setFromMatrix4(m4.makeRotationFromEuler(e)));
    }
    const seed = new Float32Array(count * 4), prm = new Float32Array(count * 4), off = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const r = Math.floor(i / per);
      const spread = 0.08 + Math.pow(rnd(), 2) * 0.55;
      seed.set([rnd(), (0.006 + rnd() * 0.012) * (rnd() < 0.5 ? 1 : -1) * 0.6, r, 0], i * 4);
      prm.set(params[r], i * 4);
      off.set([(rnd() - 0.5) * spread, (rnd() - 0.5) * spread, (rnd() - 0.5) * spread], i * 3);
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
    geo.setAttribute('aSeed', new THREE.BufferAttribute(seed, 4));
    geo.setAttribute('aParam', new THREE.BufferAttribute(prm, 4));
    geo.setAttribute('aOff', new THREE.BufferAttribute(off, 3));
    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: {
        uTime: { value: 0 }, uSize: { value: isSmall ? 0.06 : 0.055 }, uScale: { value: 400 },
        uOpacity: { value: 0 }, uColor: { value: new THREE.Vector3(0.85, 0.847, 0.83) }, uRot: { value: rot },
      },
      vertexShader: `
        uniform float uTime; uniform float uSize; uniform float uScale; uniform mat3 uRot[${ribbons}];
        attribute vec4 aSeed; attribute vec4 aParam; attribute vec3 aOff;
        void main() {
          float a = 6.2831853 * fract(aSeed.x + uTime * aSeed.y);
          vec3 p = vec3(aParam.x * cos(a), aParam.y * sin(a) * (1.0 + 0.28 * sin(3.0 * a + aParam.z)), 0.9 * sin(2.0 * a + aParam.w));
          p = uRot[int(aSeed.z + 0.5)] * p + aOff;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          gl_PointSize = uSize * (uScale / -mv.z);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uOpacity;
        void main() {
          float a = smoothstep(0.5, 0.2, length(gl_PointCoord - 0.5)) * uOpacity;
          if (a < 0.01) discard;
          gl_FragColor = vec4(uColor, a);
        }`,
    });
    const pts = new THREE.Points(geo, mat);
    pts.position.z = -2;
    pts.frustumCulled = false;
    scene.add(pts);
    return {
      resize() { mat.uniforms.uScale.value = (vh * renderer.getPixelRatio()) / 2; },
      update(t, dw) {
        mat.uniforms.uOpacity.value = 0.8 * dw;
        pts.visible = dw > 0.01;
        if (!pts.visible) return;
        mat.uniforms.uTime.value = reduced ? 0 : t;
        pts.rotation.z = Math.sin(t * 0.05) * 0.08;
      },
    };
  })();

  addGroup('hidden', (pivot) => {
    const inner = new THREE.Group();
    inner.position.set(0, -1.3, 0);
    pivot.add(inner);
    const lines = (arr, op) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3));
      const l = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: op }));
      inner.add(l); return l;
    };
    const w0 = -2.1, w1 = 2.1, h1 = 2.7;
    const grid = [];
    for (let x = w0; x <= w1 + 1e-6; x += 0.3) grid.push(x, 0, 0, x, h1, 0);
    for (let y = 0; y <= h1 + 1e-6; y += 0.3) grid.push(w0, y, 0, w1, y, 0);
    lines(grid, 0.06);
    lines([w0, 0, 0, w1, 0, 0, w1, 0, 0, w1, h1, 0, w1, h1, 0, w0, h1, 0, w0, h1, 0, w0, 0, 0, w0, 0, 0, w0, 0, 0.8, w1, 0, 0, w1, 0, 0.8], 0.35);
    // asma klozet taşıyıcı çerçevesi
    lines([1.2, 0, 0.04, 1.2, 1.12, 0.04, 1.2, 1.12, 0.04, 1.75, 1.12, 0.04, 1.75, 1.12, 0.04, 1.75, 0, 0.04, 1.2, 0.5, 0.04, 1.75, 0.5, 0.04], 0.3);

    const cold = new THREE.MeshStandardMaterial({ color: 0xE4E4E2, roughness: 0.35, metalness: 0.1, emissive: 0x2a2a2a });
    const hot = brassMat(); hot.emissive = new THREE.Color(0x3a2a14);
    const drain = new THREE.MeshStandardMaterial({ color: 0x77756F, roughness: 0.6, metalness: 0 });
    const elec = new THREE.MeshStandardMaterial({ color: 0xC9C8C4, roughness: 0.5, emissive: 0x1e1e1e });
    const defs = [
      [[[w0, 0.25, 0], [1.45, 0.25, 0], [1.45, 0.9, 0]], 0.022, cold, 0.04, 0.3],
      [[[-1.4, 0.25, 0], [-1.4, 1.1, 0]], 0.02, cold, 0.12, 0.3],
      [[[0.42, 0.25, 0], [0.42, 0.56, 0]], 0.02, cold, 0.18, 0.32],
      [[[w0, 0.38, 0], [0.58, 0.38, 0], [0.58, 0.56, 0]], 0.022, hot, 0.1, 0.38],
      [[[-1.26, 0.38, 0], [-1.26, 1.1, 0]], 0.02, hot, 0.18, 0.4],
      [[[-1.33, 0.03, 0.62], [-1.33, -0.01, 0.16], [2.1, -0.09, 0.16]], 0.05, drain, 0.3, 0.56],
      [[[0.5, 0.46, 0.02], [0.5, 0.05, 0.02], [0.5, -0.03, 0.16]], 0.035, drain, 0.36, 0.56],
      [[[1.48, 0.22, 0.06], [1.6, -0.06, 0.16]], 0.05, drain, 0.42, 0.58],
      [[[w0, 2.45, 0], [1.92, 2.45, 0], [1.92, 1.05, 0]], 0.012, elec, 0.46, 0.7],
      [[[0.92, 2.45, 0], [0.92, 1.22, 0]], 0.012, elec, 0.52, 0.72],
      [[[-0.4, 2.45, 0], [-0.4, 2.7, 0]], 0.012, elec, 0.56, 0.72],
    ];
    const pipes = defs.map(([pts, r, mat, a, b]) => {
      const m = tube(roundedPath(pts, 0.1), r, mat, r > 0.03 ? 14 : 10);
      inner.add(m);
      return { m, a, b };
    });
    const fx = [];
    const ring = (x, y, z, r = 0.07) => {
      const m = new THREE.Mesh(new THREE.TorusGeometry(r, 0.008, 8, 40), brassMat());
      m.position.set(x, y, z); inner.add(m); fx.push(m); return m;
    };
    ring(-1.33, 1.1, 0.02); ring(0.5, 0.56, 0.02); ring(1.45, 0.92, 0.02);
    const sock = (x, y) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.12, 0.03), elec);
      m.position.set(x, y, 0.02); inner.add(m); fx.push(m); return m;
    };
    sock(0.92, 1.18); sock(1.92, 1.0);
    return {
      layout: () => (aspect < 0.85
        ? { x: 0, y: 0.1, s: Math.min(0.78, (W * 0.92) / 4.4) }
        : { x: W * 0.12, y: 0.25, s: Math.min(1.1, (W * 0.42) / 4.4, (H * 0.55) / 2.9) }),
      update(t, s) {
        const p = s.p;
        for (const pp of pipes) pp.m.userData.draw(easeInOut(range(p, pp.a, pp.b)));
        const fk = easeOut(range(p, 0.25, 0.45));
        fx.forEach((m) => { m.scale.setScalar(Math.max(0.001, fk)); });
        pivot.rotation.y = -0.3 + (s.k - 0.5) * 0.45 + (reduced ? 0 : Math.sin(t * 0.3) * 0.03) + ptr.x * 0.05;
        pivot.rotation.x = 0.16 + ptr.y * 0.03;
      },
    };
  });

  /* ================= 05 · Mutfak: çalışma üçgeni ================= */
  addGroup('kitchen', (pivot) => {
    const M = { white: std(0xF5F4F1, 0.86), floor: std(0xE6E4E0, 0.92), cut: std(0x1D1D1C, 0.7, 0, 0.3), dark: std(0x2C2C2B, 0.4, 0.2, 0.6), brass: brassMat() };
    const P = maker(pivot, edgeMat(INK, 0.24));
    const B = P.box;
    B(4.8, 0.06, 3.7, M.floor, -0.05, -0.06, -0.3);
    B(4.8, 1.1, 0.1, M.white, -0.05, 0, -1.9); B(4.8, 0.014, 0.1, M.cut, -0.05, 1.1, -1.9, { edges: false });
    B(0.1, 1.1, 2.75, M.white, -2.4, 0, -0.575); B(0.1, 0.014, 2.75, M.cut, -2.4, 1.1, -0.575, { edges: false });
    B(4.2, 0.1, 0.52, M.dark, -0.15, 0, -1.55, { edges: false });
    for (let i = 0; i < 7; i++) B(0.594, 0.82, 0.58, M.white, -2.05 + i * 0.6, 0.1, -1.53);
    B(4.22, 0.04, 0.63, M.white, -0.14, 0.92, -1.53);
    B(0.58, 0.82, 0.6, M.white, -2.03, 0.1, -0.9); B(0.62, 0.04, 0.62, M.white, -2.03, 0.92, -0.9);
    B(0.62, 2.1, 0.64, M.white, -2.03, 0, -0.27);
    B(0.58, 0.82, 0.6, M.white, -2.03, 0.1, 0.36); B(0.62, 0.04, 0.62, M.white, -2.03, 0.92, 0.36);
    B(0.55, 0.012, 0.42, M.dark, 0.3, 0.962, -1.5, { edges: false });
    for (let i = 0; i < 4; i++) B(0.494, 0.82, 0.88, M.white, -0.45 + i * 0.5, 0.1, 0.45);
    B(2.1, 0.04, 1.02, M.white, 0.3, 0.92, 0.46);
    B(0.6, 0.012, 0.5, M.dark, 0.3, 0.962, 0.45, { edges: false });
    for (const x of [-0.3, 0.3, 0.9]) {
      P(new THREE.CylinderGeometry(0.16, 0.16, 0.05, 28), M.dark, x, 0.66, 1.25, { angle: 40 });
      P(new THREE.CylinderGeometry(0.014, 0.014, 0.64, 8), M.brass, x, 0.32, 1.25, { edges: false });
    }
    const S = new THREE.Vector3(0.3, 1.0, -1.5), C = new THREE.Vector3(0.3, 1.0, 0.45), F = new THREE.Vector3(-1.69, 1.0, -0.27);
    const legMat = new THREE.MeshBasicMaterial({ color: BRASS });
    const legs = [[S, C, 0.12, 0.3], [C, F, 0.28, 0.46], [F, S, 0.44, 0.62]].map(([a, b, ka, kb]) => {
      const m = tube(new THREE.LineCurve3(a, b), 0.014, legMat, 8);
      pivot.add(m);
      return { m, a, b, ka, kb, len: a.distanceTo(b) };
    });
    const nodes = [S, C, F].map((v) => {
      const m = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.012, 8, 40), legMat);
      m.position.copy(v); m.rotation.x = Math.PI / 2; pivot.add(m); return m;
    });
    // geçiş ölçüsü
    const dim = new THREE.Group(); pivot.add(dim);
    const dimMat = new THREE.MeshBasicMaterial({ color: INK });
    const dl = tube(new THREE.LineCurve3(new THREE.Vector3(1.6, 0.98, -1.21), new THREE.Vector3(1.6, 0.98, -0.01)), 0.006, dimMat, 6);
    dim.add(dl);
    for (const z of [-1.21, -0.01]) { const tk = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.006, 0.006), dimMat); tk.position.set(1.6, 0.98, z); dim.add(tk); }
    bake(pivot, new Set([...legs.map((l) => l.m), ...nodes]));
    const root = pinRoots.kitchen, sec = sections.kitchen;
    const ph = (a, b) => (p) => p > a && p < b;
    addPin(root, 'Evye', pivot, S.toArray(), { sec, show: ph(0.08, 2) });
    addPin(root, 'Ocak', pivot, C.toArray(), { sec, show: ph(0.24, 2) });
    addPin(root, 'Buzdolabı', pivot, F.toArray(), { sec, show: ph(0.4, 2) });
    legs.forEach((l) => addPin(root, fmt(l.len) + ' m', pivot, l.a.clone().lerp(l.b, 0.5).toArray(), { sec, tag: true, show: ph(l.kb - 0.04, 2) }));
    addPin(root, '120 cm geçiş', pivot, [1.6, 1.0, -0.6], { sec, tag: true, show: ph(0.78, 2) });
    return {
      layout: () => (aspect < 0.85
        ? { x: 0, y: -0.25, s: Math.min(0.66, (W * 0.95) / 4.8) }
        : { x: 0, y: -1.6, s: Math.min(1.2, (W * 0.4) / 4.8, (H * 0.44) / 3.2) }),
      update(t, s) {
        const p = s.p;
        legs.forEach((l) => l.m.userData.draw(easeInOut(range(p, l.ka, l.kb))));
        nodes.forEach((n, i) => n.scale.setScalar(Math.max(0.001, easeOut(range(p, 0.06 + i * 0.16, 0.14 + i * 0.16)))));
        const dk = easeOut(range(p, 0.72, 0.84));
        dl.userData.draw(dk); dim.visible = dk > 0.001;
        pivot.rotation.y = -0.46 + p * 0.2 + (s.k - 0.5) * 0.3 + ptr.x * 0.05;
        pivot.rotation.x = 0.92 + ptr.y * 0.03;
      },
    };
  });

  /* ================= 06 · Banyo: ıslak hacim bölgeleri ================= */
  addGroup('bath', (pivot) => {
    const M = { white: std(0xF5F4F1, 0.86), tile: std(0xECEBE8, 0.45), cut: std(0x1D1D1C, 0.7, 0, 0.3), dark: std(0x2C2C2B, 0.4, 0.2, 0.6), brass: brassMat(),
      glass: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.05, transparent: true, opacity: 0.18, depthWrite: false }),
      mirror: new THREE.MeshStandardMaterial({ color: 0xdfe2e4, roughness: 0.05, metalness: 1 }) };
    const P = maker(pivot, edgeMat(INK, 0.24));
    const B = P.box;
    B(2.7, 0.08, 2.4, M.tile, 0, -0.08, -0.05);
    B(2.7, 2.4, 0.1, M.white, 0, 0, -1.2);
    B(0.1, 2.4, 2.3, M.white, -1.3, 0, -0.1);
    {
      const pts = [];
      for (let x = -1.25; x <= 1.35; x += 0.3) pts.push(x, 0.002, -1.15, x, 0.002, 1.15);
      for (let z = -1.15; z <= 1.15; z += 0.3) pts.push(-1.25, 0.002, z, 1.35, 0.002, z);
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      pivot.add(new THREE.LineSegments(g, edgeMat(INK, 0.1)));
    }
    B(0.012, 2.0, 0.7, M.glass, -0.34, 0, -0.8, { edge: edgeMat(INK, 0.35) });
    B(0.62, 0.006, 0.06, M.dark, -0.8, 0.002, -0.25, { edges: false });
    P(new THREE.CylinderGeometry(0.1, 0.1, 0.014, 28), M.brass, -0.8, 2.05, -0.95, { edges: false });
    P(new THREE.CylinderGeometry(0.008, 0.008, 0.22, 8), M.brass, -0.8, 2.05, -1.06, { edges: false, rx: Math.PI / 2 });
    P(new THREE.BoxGeometry(0.12, 0.12, 0.04), M.brass, -0.8, 1.05, -1.13, { edges: false });
    B(0.8, 0.42, 0.46, M.white, 0.65, 0.42, -0.92);
    B(0.82, 0.05, 0.48, M.white, 0.65, 0.84, -0.92);
    B(0.42, 0.012, 0.3, M.dark, 0.65, 0.89, -0.9, { edges: false });
    B(0.7, 0.8, 0.015, M.mirror, 0.65, 1.12, -1.14, { edges: false });
    P(new RoundedBoxGeometry(0.36, 0.34, 0.54, 4, 0.1), M.white, -1.0, 0.42, 0.75, { angle: 50, ry: Math.PI / 2 });
    for (let i = 0; i < 6; i++) B(0.5, 0.025, 0.03, M.brass, 0.0, 0.7 + i * 0.12, -1.12, { edges: false });

    const zone = (x0, x1, z0, z1, h, color, op, eop) => {
      const geo = new THREE.BoxGeometry(x1 - x0, h, z1 - z0);
      geo.translate(0, h / 2, 0);
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: op, depthWrite: false }));
      m.position.set((x0 + x1) / 2, 0.004, (z0 + z1) / 2);
      m.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color, transparent: true, opacity: eop })));
      pivot.add(m); return m;
    };
    const z0 = zone(-1.25, -0.34, -1.15, -0.25, 0.08, BRASS, 0.55, 0.9);
    const z1 = zone(-1.25, -0.34, -1.15, -0.25, 2.25, BRASS, 0.16, 0.8);
    const z2 = zone(-1.25, 0.26, -1.15, 0.35, 2.25, 0x7A7873, 0.07, 0.45);
    // su yalıtımı: duşta 200 cm, diğer duvar diplerinde 15 cm
    const memMat = new THREE.MeshStandardMaterial({ color: 0x7E8E9A, roughness: 0.7, transparent: true, opacity: 0.9 });
    const mem = (w, h, d, x, z) => { const g = new THREE.BoxGeometry(w, h, d); g.translate(0, h / 2, 0); const m = new THREE.Mesh(g, memMat); m.position.set(x, 0.002, z); pivot.add(m); return m; };
    const mems = [mem(0.91, 2.0, 0.012, -0.795, -1.143), mem(0.012, 2.0, 0.9, -1.243, -0.7), mem(1.69, 0.15, 0.012, 0.495, -1.143), mem(0.012, 0.15, 1.4, -1.243, 0.45)];
    bake(pivot, new Set([z0, z1, z2, ...mems]));
    const root = pinRoots.bath, sec = sections.bath;
    const ph = (a) => (p) => p > a;
    addPin(root, 'Bölge 0 · IPX7', pivot, [-1.25, 0.08, -0.25], { sec, show: ph(0.12) });
    addPin(root, 'Bölge 1 · IPX4', pivot, [-0.34, 1.15, -0.25], { sec, show: ph(0.3) });
    addPin(root, 'Bölge 2 · IPX4', pivot, [0.26, 2.25, 0.35], { sec, show: ph(0.46) });
    addPin(root, 'Yalıtım 200 cm', pivot, [-1.24, 2.0, -0.7], { sec, tag: true, show: ph(0.64) });
    addPin(root, 'Süzgeç · %2 eğim', pivot, [-0.55, 0.0, 0.05], { sec, tag: true, show: ph(0.76) });
    return {
      layout: () => (aspect < 0.85
        ? { x: 0, y: -0.6, s: Math.min(0.78, (W * 0.86) / 3.6) }
        : { x: 0, y: -2.25, s: Math.min(1.05, (W * 0.36) / 3.6, (H * 0.42) / 2.6) }),
      update(t, s) {
        const p = s.p;
        z0.scale.y = Math.max(0.001, easeOut(range(p, 0.06, 0.18)));
        z1.scale.y = Math.max(0.001, easeOut(range(p, 0.2, 0.38)));
        z2.scale.y = Math.max(0.001, easeOut(range(p, 0.36, 0.54)));
        const mk = easeOut(range(p, 0.56, 0.72));
        mems.forEach((m) => { m.scale.y = Math.max(0.001, mk); m.visible = mk > 0.001; });
        pivot.rotation.y = -0.72 + p * 0.2 + (s.k - 0.5) * 0.3 + ptr.x * 0.05;
        pivot.rotation.x = 0.4 + ptr.y * 0.03;
      },
    };
  });

  /* ================= 07 · Mobilya: patlatılmış gardırop ================= */
  addGroup('furniture', (pivot) => {
    const M = { white: std(0xF5F4F1, 0.8), board: std(0xEAE7E1, 0.85), dark: std(0x2C2C2B, 0.4, 0.2, 0.6), brass: brassMat(), back: std(0xDCD8D1, 0.9) };
    const P = maker(pivot, edgeMat(INK, 0.26));
    const parts = [];
    const part = (w, h, d, mat, x, y, z, ex, o) => {
      const m = P(new THREE.BoxGeometry(w, h, d), mat, x, y, z, o);
      parts.push({ m, base: m.position.clone(), ex: new THREE.Vector3(...ex) });
      return m;
    };
    const t = 0.018;
    const sideR = part(t, 2.1, 0.56, M.board, 0.591, 1.15, 0, [0.42, 0, 0]);
    part(t, 2.1, 0.56, M.board, -0.591, 1.15, 0, [-0.42, 0, 0]);
    part(1.164, t, 0.56, M.board, 0, 2.191, 0, [0, 0.34, 0]);
    part(1.164, t, 0.56, M.board, 0, 0.109, 0, [0, -0.12, 0]);
    part(1.164, 0.1, 0.02, M.dark, 0, 0.05, 0.24, [0, -0.25, 0.1], { edges: false });
    const back = part(1.164, 2.09, 0.008, M.back, 0, 1.15, -0.276, [0, 0, -0.55]);
    part(t, 2.064, 0.54, M.board, 0, 1.15, 0, [0, 0, -0.18]);
    part(0.564, t, 0.52, M.board, -0.291, 1.45, 0, [0, 0, 0.16]);
    part(0.564, t, 0.52, M.board, -0.291, 1.85, 0, [0, 0, 0.16]);
    const rail = P(new THREE.CylinderGeometry(0.012, 0.012, 0.56, 10), M.brass, 0.291, 1.92, 0, { edges: false, rz: Math.PI / 2 });
    parts.push({ m: rail, base: rail.position.clone(), ex: new THREE.Vector3(0, 0, 0.12) });
    // çekmece
    const drawer = new THREE.Group(); drawer.position.set(-0.291, 0.32, 0.02); pivot.add(drawer);
    const DP = maker(drawer, edgeMat(INK, 0.26));
    DP(new THREE.BoxGeometry(0.556, 0.2, t), M.white, 0, 0, 0.27);
    DP(new THREE.BoxGeometry(0.012, 0.16, 0.48), M.board, -0.26, -0.01, 0.02);
    DP(new THREE.BoxGeometry(0.012, 0.16, 0.48), M.board, 0.26, -0.01, 0.02);
    DP(new THREE.BoxGeometry(0.52, 0.16, 0.012), M.board, 0, -0.01, -0.22);
    DP(new THREE.BoxGeometry(0.52, 0.008, 0.48), M.board, 0, -0.09, 0.02);
    for (const x of [-0.272, 0.272]) DP(new THREE.BoxGeometry(0.008, 0.02, 0.46), M.dark, x, -0.06, 0.01, { edges: false });
    parts.push({ m: drawer, base: drawer.position.clone(), ex: new THREE.Vector3(0, 0, 0.5) });
    // kapaklar
    const doors = [];
    for (const sx of [-1, 1]) {
      const d = part(0.596, 2.088, t, M.white, sx * 0.299, 1.15, 0.29, [sx * 0.16, 0, 1.05]);
      P(new THREE.BoxGeometry(0.012, 0.36, 0.012), M.brass, -sx * 0.27, 0.1, 0.018, { parent: d, edges: false });
      doors.push(d);
    }
    // menteşeler (35 mm kap)
    const hinges = [];
    for (const sx of [-1, 1]) for (const y of [0.3, 1.15, 2.0]) {
      const h = P(new THREE.CylinderGeometry(0.0175, 0.0175, 0.014, 20), M.brass, sx * 0.55, y, 0.27, { edges: false, rx: Math.PI / 2 });
      parts.push({ m: h, base: h.position.clone(), ex: new THREE.Vector3(sx * 0.08, 0, 0.62) });
      hinges.push(h);
    }
    const root = pinRoots.furniture, sec = sections.furniture;
    const ph = (a) => (p) => p > a;
    addPin(root, 'Gövde · 18 mm', sideR, [0, 0.7, 0.28], { sec, show: ph(0.3) });
    addPin(root, 'Arkalık · 8 mm', back, [-0.4, 0.6, 0], { sec, show: ph(0.4) });
    addPin(root, 'Kapak · 18 mm', doors[1], [0.2, -0.5, 0.01], { sec, show: ph(0.5) });
    addPin(root, 'Menteşe · 110°', hinges[4], [0, 0, 0], { sec, show: ph(0.6) });
    addPin(root, 'Ray · 40 kg', drawer, [0.27, -0.06, 0.22], { sec, show: ph(0.7) });
    return {
      layout: () => (aspect < 0.85
        ? { x: -W * 0.08, y: -1.25, s: Math.min(0.92, (W * 0.62) / 2.6) }
        : { x: 0, y: -2.25, s: Math.min(1.3, (H * 0.48) / 2.4) }),
      update(tt, s) {
        const p = s.p;
        const e = easeInOut(range(p, 0.08, 0.6));
        for (const pt of parts) pt.m.position.copy(pt.base).addScaledVector(pt.ex, e);
        pivot.rotation.y = 0.62 - p * 0.25 - (s.k - 0.5) * 0.3 + ptr.x * 0.05;
        pivot.rotation.x = 0.14 + ptr.y * 0.03;
      },
    };
  });

  /* ================= 08 · Malzeme: tezgâh adası ================= */
  const SURF = {
    marble: { size: 1024, base: [240, 238, 234], rough: 0.22, cc: 0.7, ccr: 0.15, env: 0.9 },
    quartz: { size: 512, base: [229, 228, 225], rough: 0.3, cc: 0.4, ccr: 0.3, env: 0.85 },
    porcelain: { size: 1024, base: [50, 50, 49], rough: 0.28, cc: 0.5, ccr: 0.25, env: 0.9 },
    oak: { size: 512, base: [176, 136, 94], rough: 0.62, cc: 0.04, ccr: 0.8, env: 0.7 },
  };
  // Dört yüzey aynı gölgelendiriciyi paylaşır (hepsi clearcoat'lu Physical): malzeme değişince derleme yapılmaz.
  const surfMats = {};
  for (const [k, o] of Object.entries(SURF)) {
    surfMats[k] = new THREE.MeshPhysicalMaterial({ map: placeholderTex(o.base), roughness: o.rough, clearcoat: o.cc, clearcoatRoughness: o.ccr, envMapIntensity: o.env });
  }
  const surfFor = (k) => surfMats[k];

  const matGroup = addGroup('material', (pivot) => {
    const body = std(0x2A2A29, 0.55, 0.05, 0.6);
    const P = maker(pivot, edgeMat(0x000000, 0.35));
    for (let i = 0; i < 4; i++) P.box(0.546, 0.78, 0.86, body, -0.825 + i * 0.55, 0.08, 0);
    P.box(2.2, 0.08, 0.8, std(0x161615, 0.6), 0, 0, -0.02, { edges: false });
    const slabs = new THREE.Group(); pivot.add(slabs);
    const SP = maker(slabs, null);
    const top = SP(new THREE.BoxGeometry(2.36, 0.04, 1.0), surfFor('marble'), 0, 0.88, 0);
    const sideL = SP(new THREE.BoxGeometry(0.04, 0.88, 1.0), surfFor('marble'), -1.16, 0.44, 0);
    const sideR = SP(new THREE.BoxGeometry(0.04, 0.88, 1.0), surfFor('marble'), 1.16, 0.44, 0);
    // kütüphane objeleri
    const brass = brassMat();
    P(new THREE.CylinderGeometry(0.11, 0.07, 0.07, 32, 1, true), brass, 0.55, 0.935, 0.12, { edges: false });
    P(new THREE.CylinderGeometry(0.045, 0.05, 0.26, 24), std(0xEDEBE6, 0.4), -0.6, 1.03, -0.18, { edges: false });
    // gölge
    const sh = canvasTex(renderer, 128, (ctx, w) => {
      const g = ctx.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
      g.addColorStop(0, 'rgba(0,0,0,.35)'); g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, w);
    }, { srgb: false });
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 2.6), new THREE.MeshBasicMaterial({ map: sh, transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.001; pivot.add(shadow);
    bake(pivot);
    let cur = 'marble', target = 'marble', swapT = 1, swapped = true;
    return {
      setTarget(k) { if (k !== target) { target = k; swapT = 0; swapped = false; } },
      layout: () => (aspect < 0.85
        ? { x: 0, y: -0.1, s: Math.min(0.95, (W * 0.88) / 2.5) }
        : { x: 0, y: -0.75, s: Math.min(1.6, (W * 0.36) / 2.5, (H * 0.4) / 1.1) }),
      update(t, s, ctx) {
        if (swapT < 1) {
          swapT = Math.min(1, swapT + (reduced ? 1 : 1 / 42));
          if (!swapped && swapT >= 0.5) {
            cur = target; swapped = true;
            const m = surfFor(cur); top.material = m; sideL.material = m; sideR.material = m;
          }
          slabs.position.y = Math.sin(swapT * Math.PI) * 0.22;
          slabs.rotation.z = Math.sin(swapT * Math.PI) * 0.02;
        } else if (target !== cur) { swapT = 0; swapped = false; }
        pivot.rotation.y = -0.45 + (reduced ? 0 : Math.sin(t * 0.3) * 0.4) + (s.k - 0.5) * 0.5 + ptr.x * 0.06;
        pivot.rotation.x = 0.3 + ptr.y * 0.03;
      },
    };
  });

  // Dokular Worker'da üretilir; geldikçe yer tutucuların yerine geçer ve hemen GPU'ya yüklenir
  const texturer = makeTexturer();
  const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const applyTex = (mat, buf, size) => {
    const t = dataTex(buf, size);
    t.anisotropy = maxAniso;
    mat.map = t;
    renderer.initTexture(t);
  };
  const texJobs = [
    texturer('plaster', 256).then((b) => plasterMat && applyTex(plasterMat, b, 256)),
    ...Object.entries(SURF).map(([k, o]) => texturer(k, o.size).then((b) => applyTex(surfMats[k], b, o.size))),
  ];

  /* ------------------------------------------------------------------ */
  const v3 = new THREE.Vector3();
  function placePins() {
    for (const pin of pins) {
      const s = pin.sec;
      const show = s.vis && pin.show(s.p);
      if (show !== pin.on) { pin.on = show; pin.el.classList.toggle('off', !show); }
      if (!show) continue;
      pin.obj.localToWorld(v3.copy(pin.local));
      v3.project(camera);
      const x = (v3.x * 0.5 + 0.5) * vw;
      const y = (-v3.y * 0.5 + 0.5) * vh - s.offPx;
      const tf = pin.tag
        ? `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0) translate(-50%,-50%)`
        : `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0) translate(-4px,-50%)`;
      if (tf !== pin.tf) { pin.tf = tf; pin.el.style.transform = tf; }
    }
  }

  // Kareler sürekli yavaşsa çözünürlüğü kademeli düşür (en az 1)
  let lastNow = 0, slow = 0, cool = 0;
  function adapt() {
    const now = performance.now();
    const dt = lastNow ? now - lastNow : 16;
    lastNow = now;
    if (dt > 250 || dpr <= 1) return;
    if (cool > 0) { cool--; return; }
    slow = dt > 24 ? slow + 1 : Math.max(0, slow - 1);
    if (slow > 40) {
      dpr = Math.max(1, Math.round((dpr - 0.25) * 100) / 100);
      renderer.setPixelRatio(dpr);
      particles.resize();
      slow = 0; cool = 90;
    }
  }

  let parked = false;
  const api = {
    // Tuval 100lvh: mobilde adres çubuğu açılıp kapanırken boyut değişmez, yeniden ayırma yapılmaz
    resize() {
      ana.invalidate();
      const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
      if (w === vw && h === vh) return;
      vw = w; vh = h; aspect = w / h;
      renderer.setSize(w, h, false);
      camera.aspect = aspect;
      camera.updateProjectionMatrix();
      H = 2 * camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      W = H * aspect;
      particles.resize();
    },
    setMaterial(i) { const k = mats[i] && mats[i].tex; if (k) matGroup.setTarget(k); },
    pickLayer(x, y) { return sections.anatomy.vis ? ana.pick(x, y, sections.anatomy.p) : -1; },
    update(t, ctx) {
      ptr.x = lerp(ptr.x, ctx.pointer.x, 0.05);
      ptr.y = lerp(ptr.y, ctx.pointer.y, 0.05);
      let any = ctx.dw > 0.01;
      for (const g of groups) {
        const s = g.sec;
        g.root.visible = s.vis;
        if (!s.vis) continue;
        any = true;
        const L = g.layout();
        g.root.position.set(L.x, L.y - (s.offPx / vh) * H, 0);
        g.root.scale.setScalar(L.s);
        g.update(t, s, ctx);
      }
      if (!any) {
        // 3D'siz bölümlerde hiç çizme; tuvali de birleştiriciden çıkar
        if (!parked) { renderer.clear(); canvas.style.visibility = 'hidden'; parked = true; }
        placePins();
        lastNow = 0;
        return;
      }
      if (parked) { canvas.style.visibility = ''; parked = false; }
      particles.update(t, ctx.dw);
      scene.updateMatrixWorld();
      if (sections.anatomy.vis) ana.place(sections.anatomy);
      placePins();
      renderer.render(scene, camera);
      adapt();
    },
  };
  if (/[?&]perf\b/.test(location.search)) {
    window.__scene = { info: () => ({ calls: renderer.info.render.calls, tris: renderer.info.render.triangles, dpr }) };
  }

  // Yükleme ekranı açıkken: dokuları bekle (en çok 3 sn), gölgelendiricileri derle ve
  // her şeyi bir kez çiz ki geometriler ve dokular GPU'ya şimdi yüklensin, bölüm geçişlerinde değil.
  await Promise.race([Promise.all(texJobs), new Promise((r) => setTimeout(r, 3000))]);
  api.resize();
  const hidden = [];
  scene.traverse((o) => { if (!o.visible) { hidden.push(o); o.visible = true; } });
  try {
    if (renderer.compileAsync && renderer.extensions.has('KHR_parallel_shader_compile')) await renderer.compileAsync(scene, camera);
    else renderer.compile(scene, camera);
  } catch (e) { /* derleme render sırasında yapılır */ }
  renderer.render(scene, camera);
  hidden.forEach((o) => { o.visible = false; });
  groups.forEach((g) => { g.root.visible = false; });
  renderer.clear();
  return api;
}
