// 3910 W Dupont Rd — site model viewer.
// Site frame (metres): x east, y north, z up (NAVD88).  three.js: X = x, Y = z - z0, Z = -y.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { sunPosition, easternToUTC, formatDay, formatClock, daylight, compassPoint } from './sun.js';

const $ = (id) => document.getElementById(id);
const FT = 0.3048;
const INK = 0x1c2630;
const ACCENT = 0xe4572e;
const YEAR = new Date().getFullYear();
// ?assets=dir lets a build be previewed from another same-origin folder (plain relative paths only)
const ASSETS = (() => {
  const a = new URLSearchParams(location.search).get('assets');
  return a && /^[\w][\w\-/]*$/.test(a) ? a.replace(/\/+$/, '') : 'assets';
})();

const state = {
  mode: 'photo', minutes: 15 * 60, day: 172, playing: false, measuring: false,
  layers: {}, selected: null, measure: [], dirty: true, year: null,
};
let M;                       // manifest
let z0 = 0;
let renderer, scene, camera, controls, sun, hemi, fill;
let terrain, terrainMat, skirt, heights;
const groups = {};           // layer id -> Object3D
const modelMeshes = [];      // pickable meshes
const tags = [];             // {el, pos:Vector3, layer}
const noCanopy = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
noCanopy.needsUpdate = true;
const terrainUniforms = { uContour: { value: 0 }, uInterval: { value: FT }, uSummer: { value: 0 }, uCanopy: { value: noCanopy }, uCanopyOn: { value: 0 } };

const v3 = (x, y, z) => new THREE.Vector3(x, z - z0, -y);

// ---------------------------------------------------------------- boot ------
init().catch((err) => {
  console.error(err);
  const msg = $('loading-msg');
  msg.textContent = `Could not load the model: ${err.message}`;
  msg.classList.add('err');
});

async function init() {
  if (new URLSearchParams(location.search).get('ui') === '0') document.body.classList.add('bare');
  if (!hasWebGL()) throw new Error('this browser has WebGL turned off or unavailable');
  M = await fetchJSON(`${ASSETS}/manifest.json`);
  z0 = M.z0;
  setupChrome();
  setupScene();

  const jobs = [
    ['terrain', loadTerrain()],
    ['aerial photo', loadOrtho()],
    ['trees', loadTrees()],
    ...M.models.map((m) => [m.label || m.id, loadModel(m)]),
  ];
  let done = 0;
  const bar = $('bar');
  await Promise.all(jobs.map(([name, p]) => p.then(() => {
    done += 1;
    bar.style.width = `${Math.round((done / jobs.length) * 100)}%`;
    $('loading-msg').textContent = `Loaded ${name}`;
  })));

  buildParcel();
  buildTags();
  buildDimensions();
  setupLayers();
  setupEpochs();
  setupViews();
  setupModes();
  setupSun();
  setupPicking();
  setupMeasure();
  setupAbout();
  applyMode('photo');
  goToView(M.views[0], false);
  onResize();
  window.addEventListener('resize', onResize);
  renderer.setAnimationLoop(tick);
  requestAnimationFrame(() => $('loading').classList.add('done'));
  window.__viewer = { state, M, scene, camera, controls, goToView, applyMode, setLayer, setSun, renderOnce };
}

function hasWebGL() {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
}
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return r.json();
}
async function fetchBuffer(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return r.arrayBuffer();
}

// --------------------------------------------------------------- chrome -----
function setupChrome() {
  document.title = `${M.title} — 3D site model`;
  $('tb-eyebrow').textContent = M.eyebrow || 'Site model';
  $('tb-title').textContent = M.title;
  $('tb-sub').textContent = M.subtitle;
  const dl = $('tb-facts');
  for (const f of M.facts || []) {
    const d = document.createElement('div');
    d.innerHTML = `<dt></dt><dd></dd>`;
    d.firstChild.textContent = f.k;
    d.lastChild.textContent = f.v;
    d.lastChild.title = f.v;
    if (f.wide) d.classList.add('wide');
    dl.append(d);
  }
  $('foot-note').textContent = M.foot_note || '';
  const tb = $('titleblock');
  const setH = () => document.documentElement.style.setProperty('--tb-h', `${tb.offsetHeight}px`);
  new ResizeObserver(setH).observe(tb);
  setH();
  const panel = $('panel');
  const toggle = $('panel-toggle');
  panel.dataset.open = 'false';
  toggle.addEventListener('click', () => {
    const open = panel.dataset.open !== 'true';
    panel.dataset.open = String(open);
    toggle.setAttribute('aria-expanded', String(open));
  });
  if (M.download) { $('download').href = M.download.replace(/^assets\//, `${ASSETS}/`); } else { $('download').hidden = true; }
}

// ---------------------------------------------------------------- scene -----
function setupScene() {
  const canvas = $('scene');
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.setClearColor(0x000000, 0);

  scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.35;

  camera = new THREE.PerspectiveCamera(38, 1, 0.5, 4000);
  controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = false;
  controls.minDistance = 6;
  controls.maxDistance = 900;
  controls.maxPolarAngle = Math.PI * 0.495;
  controls.zoomToCursor = true;
  controls.addEventListener('change', () => { state.dirty = true; });
  controls.addEventListener('start', () => { state.roll = 0; state.exact = false; state.dirty = true; });   // taking hold of the view levels it

  sun = new THREE.DirectionalLight(0xffffff, 3.0);
  sun.castShadow = true;
  const small = Math.min(window.innerWidth, window.innerHeight) < 700;
  sun.shadow.mapSize.set(small ? 2048 : 4096, small ? 2048 : 4096);
  const sc = sun.shadow.camera;
  sc.left = -75; sc.right = 75; sc.top = 75; sc.bottom = -75; sc.near = 10; sc.far = 700;
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.06;
  sun.shadow.radius = 2.2;
  scene.add(sun, sun.target);
  hemi = new THREE.HemisphereLight(0xdfe9f2, 0x8a8674, 1.1);
  scene.add(hemi);
  fill = new THREE.DirectionalLight(0xffffff, 0.25);
  scene.add(fill);
}

// -------------------------------------------------------------- terrain -----
async function loadTerrain() {
  const t = M.terrain;
  const buf = await fetchBuffer(`${ASSETS}/${t.file}`);
  const raw = new Uint16Array(buf);
  const { nx, ny } = t;
  heights = new Float32Array(nx * ny);
  const pos = new Float32Array(nx * ny * 3);
  const uv = new Float32Array(nx * ny * 2);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const k = j * nx + i;
      const z = t.zmin + raw[k] * t.zscale;
      heights[k] = z;
      pos[3 * k] = t.x0 + i * t.cell;
      pos[3 * k + 1] = z - z0;
      pos[3 * k + 2] = -(t.y0 + j * t.cell);
      uv[2 * k] = i / (nx - 1);
      uv[2 * k + 1] = j / (ny - 1);
    }
  }
  const idx = new Uint32Array((nx - 1) * (ny - 1) * 6);
  let n = 0;
  for (let j = 0; j < ny - 1; j++) {
    for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
      idx[n++] = a; idx[n++] = b; idx[n++] = c;
      idx[n++] = b; idx[n++] = d; idx[n++] = c;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  geo.computeVertexNormals();

  terrainMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, metalness: 0 });
  terrainMat.onBeforeCompile = (shader) => {
    shader.uniforms.uContour = terrainUniforms.uContour;
    shader.uniforms.uInterval = terrainUniforms.uInterval;
    shader.uniforms.uSummer = terrainUniforms.uSummer;
    shader.uniforms.uCanopy = terrainUniforms.uCanopy;
    shader.uniforms.uCanopyOn = terrainUniforms.uCanopyOn;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying float vHeight;\nvarying vec2 vTerrUv;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvHeight = position.y;\nvTerrUv = uv;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', '#include <common>\nvarying float vHeight;\nuniform float uContour;\nuniform float uInterval;\nuniform float uSummer;\nuniform sampler2D uCanopy;\nuniform float uCanopyOn;\nvarying vec2 vTerrUv;')
      .replace('#include <color_fragment>', `#include <color_fragment>
        if (uSummer > 0.0) {
          // the aerial photograph is from early March: in the growing season dormant grass, leaf litter and
          // stubble (warm, some colour) go green; paving, roofs and water (neutral or bright) are left alone
          vec3 c0 = diffuseColor.rgb;
          float lum = dot(c0, vec3(0.2126, 0.7152, 0.0722));
          float mx = max(c0.r, max(c0.g, c0.b));
          float mn = min(c0.r, min(c0.g, c0.b));
          float sat = (mx - mn) / max(mx, 0.001);
          float veg = smoothstep(0.05, 0.17, sat) * (1.0 - smoothstep(0.62, 0.85, lum)) * step(c0.b, max(c0.r, c0.g) + 0.01);
          // the long blue-violet shadows of bare trees (green is their weakest channel; asphalt is neutral, water is all blue)
          float violet = smoothstep(1.04, 1.14, (c0.r + c0.b) * 0.5 / max(c0.g, 0.02)) * (1.0 - smoothstep(0.3, 0.55, lum))
            * (1.0 - smoothstep(1.25, 1.5, c0.b / max(c0.r, 0.02)));
          veg = max(veg, violet);
          vec3 green = vec3(0.24, 0.36, 0.13) * (0.4 + lum * 1.5);
          diffuseColor.rgb = mix(c0, green, veg * 0.8 * uSummer);
          // under the trees the floor is in leaf shade, whatever the March photograph shows there
          float can = texture2D(uCanopy, vTerrUv).r * uCanopyOn * uSummer;
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.085, 0.14, 0.06) * (0.75 + lum), can * 0.88);
        }
        if (uContour > 0.0) {
          float f = vHeight / uInterval;
          float w = fwidth(f);
          float l1 = 1.0 - smoothstep(0.0, w * 1.3, abs(fract(f - 0.5) - 0.5));
          float f5 = f / 5.0;
          float w5 = fwidth(f5);
          float l5 = 1.0 - smoothstep(0.0, w5 * 2.4, abs(fract(f5 - 0.5) - 0.5));
          float fade = 1.0 - smoothstep(0.25, 0.6, w);
          diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.085, 0.12, 0.16), max(l1 * 0.42 * fade, l5 * 0.7) * uContour);
        }`);
  };
  terrain = new THREE.Mesh(geo, terrainMat);
  terrain.receiveShadow = true;
  terrain.name = 'terrain';
  scene.add(terrain);

  // model-base skirt: the site reads as a physical block cut out of the ground
  let lowest = Infinity;                                       // a loop, not Math.min(...heights): 125,000 arguments overflow the call stack in some browsers
  for (let i = 0; i < heights.length; i++) if (heights[i] < lowest) lowest = heights[i];
  const base = lowest - z0 - 7;
  const ring = [];
  for (let i = 0; i < nx; i++) ring.push(i);
  for (let j = 1; j < ny; j++) ring.push(j * nx + nx - 1);
  for (let i = nx - 2; i >= 0; i--) ring.push((ny - 1) * nx + i);
  for (let j = ny - 2; j > 0; j--) ring.push(j * nx);
  const sp = new Float32Array(ring.length * 6);
  const si = [];
  ring.forEach((k, r) => {
    sp.set([pos[3 * k], pos[3 * k + 1], pos[3 * k + 2], pos[3 * k], base, pos[3 * k + 2]], r * 6);
    const a = r * 2, b = ((r + 1) % ring.length) * 2;
    si.push(a, a + 1, b, b, a + 1, b + 1);
  });
  const sg = new THREE.BufferGeometry();
  sg.setAttribute('position', new THREE.BufferAttribute(sp, 3));
  sg.setIndex(si);
  sg.computeVertexNormals();
  skirt = new THREE.Mesh(sg, new THREE.MeshStandardMaterial({ color: 0x8f8a7c, roughness: 1, side: THREE.DoubleSide }));
  scene.add(skirt);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry((nx - 1) * t.cell, (ny - 1) * t.cell),
    new THREE.MeshStandardMaterial({ color: 0x8f8a7c, roughness: 1, side: THREE.BackSide }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(t.x0 + ((nx - 1) * t.cell) / 2, base, -(t.y0 + ((ny - 1) * t.cell) / 2));
  scene.add(floor);
}

function groundZ(x, y) {
  const t = M.terrain;
  const fx = Math.min(Math.max((x - t.x0) / t.cell, 0), t.nx - 1.001);
  const fy = Math.min(Math.max((y - t.y0) / t.cell, 0), t.ny - 1.001);
  const i = Math.floor(fx), j = Math.floor(fy), tx = fx - i, ty = fy - j;
  const h = (a, b) => heights[b * t.nx + a];
  return h(i, j) * (1 - tx) * (1 - ty) + h(i + 1, j) * tx * (1 - ty) + h(i, j + 1) * (1 - tx) * ty + h(i + 1, j + 1) * tx * ty;
}

let orthoTex = null;
const orthoCache = {};
async function loadOrthoFile(file) {
  if (!orthoCache[file]) {
    orthoCache[file] = new THREE.TextureLoader().loadAsync(`${ASSETS}/${file}`).then((t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      return t;
    });
  }
  return orthoCache[file];
}
async function loadOrtho() {
  if (!M.ortho) return;
  orthoTex = await loadOrthoFile(M.ortho.file);
  state.year = M.ortho.year ?? null;
}

// The ground photo can be swapped for older surveys; buildings newer than the photo are hidden.
function setupEpochs() {
  const epochs = M.ortho_epochs || [];
  if (epochs.length < 2) return;
  const row = $('epoch-row');
  $('epochs').hidden = false;
  const note = document.createElement('p');
  note.id = 'epoch-note';
  $('epochs').append(note);
  for (const e of epochs) {
    const b = document.createElement('button');
    b.textContent = e.year;
    b.dataset.year = e.year;
    b.setAttribute('role', 'radio');
    b.title = e.date ? `Photographed ${e.date}` : `Photographed ${e.year}`;
    b.addEventListener('click', () => setEpoch(e));
    row.append(b);
  }
  markEpoch();
}
function markEpoch() {
  for (const b of $('epoch-row').children) b.setAttribute('aria-checked', String(+b.dataset.year === state.year));
  const hidden = M.models.filter((m) => !m.quiet && !builtBy(m)).map((m) => `${m.label.toLowerCase()} (${m.since})`);
  const later = M.models.filter((m) => !m.quiet && builtBy(m) && (m.since || 0) > state.year).map((m) => `${m.label.toLowerCase()} (${m.since})`);
  const e = (M.ortho_epochs || []).find((x) => x.year === state.year);
  const when = e?.date ? `Photo: ${e.date}.` : '';
  const n = $('epoch-note');
  if (n) n.textContent = hidden.length ? `${when} Not yet built: ${hidden.join(', ')}.` : later.length ? `${when} Built since: ${later.join(', ')}.` : when;
  $('mode-hint').textContent = MODE_HINT[state.mode]();
  const aerialNote = document.querySelector('input[data-layer="aerial"]')?.closest('li')?.querySelector('small');
  if (aerialNote) aerialNote.textContent = String(state.year);
  for (const t of tags) if (t.since) t.visible = builtBy({ since: t.since });
}
async function setEpoch(e) {
  const tex = await loadOrthoFile(e.file);
  orthoTex = tex;
  state.year = e.year;
  if (state.layers.aerial === false) setLayer('aerial', true, true);
  if (state.mode !== 'photo') applyMode('photo');
  markEpoch();
  syncModels();
  applyTerrainLook();
  state.dirty = true;
}
// The newest photograph stands for "today": everything is shown on it, including what was
// built after it was taken. Older photographs hide whatever did not exist yet.
function builtBy(m) {
  const years = (M.ortho_epochs || []).map((e) => e.year);
  const latest = years.length ? Math.max(...years) : null;
  return state.year === null || state.year === latest || (m.since || 0) <= state.year;
}
function syncModels() {
  for (const m of M.models) {
    const g = groups[m.id];
    if (g) g.visible = state.layers[m.layer || m.id] !== false && builtBy(m);
  }
}

// ---------------------------------------------------------------- trees -----
async function loadTrees() {
  const t = M.trees;
  const g = new THREE.Group();
  g.name = 'trees';
  groups.trees = g;
  scene.add(g);
  if (!t || !t.count) return;
  const data = new Float32Array(await fetchBuffer(`${ASSETS}/${t.file}`));
  const n = Math.floor(data.length / 6);
  const kinds = [[], []];
  for (let i = 0; i < n; i++) kinds[data[i * 6 + 5] >= 0.5 ? 1 : 0].push(i);
  const trunkGeo = new THREE.CylinderGeometry(0.5, 0.75, 1, 6, 1, true).translate(0, 0.5, 0);
  const trunkMat = new THREE.MeshStandardMaterial({ color: 0x5b5246, roughness: 1 });
  const crownGeos = [new THREE.IcosahedronGeometry(1, 1), new THREE.ConeGeometry(1, 1, 9, 1).translate(0, 0.5, 0)];
  const crownMats = [
    new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true }),
    new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true }),
  ];
  [trunkMat, ...crownMats].forEach(cutaway);
  const m4 = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const col = new THREE.Color();
  const up = new THREE.Vector3(0, 1, 0);
  kinds.forEach((list, kind) => {
    if (!list.length) return;
    const trunks = new THREE.InstancedMesh(trunkGeo, trunkMat, list.length);
    const crowns = new THREE.InstancedMesh(crownGeos[kind], crownMats[kind], list.length);
    const tops = new Float32Array(list.length);
    list.forEach((ti, k) => {
      const [x, y, gz, h, r] = data.subarray(ti * 6, ti * 6 + 5);
      const rnd = fract(Math.sin(ti * 12.9898) * 43758.5453);
      tops[k] = gz + h - z0;
      q.setFromAxisAngle(up, rnd * 6.283);
      if (kind === 0) {
        const trunkH = h * 0.45;                                   // woodland hardwoods: clear stem, crown over the upper two thirds
        const tr = Math.max(0.14, h * 0.016);
        m4.compose(v3(x, y, gz), q, new THREE.Vector3(tr, trunkH, tr));
        trunks.setMatrixAt(k, m4);
        const ch = h * 0.66;
        const cr = r * (1.05 + rnd * 0.2);
        m4.compose(v3(x, y, gz + h - ch / 2), q, new THREE.Vector3(cr, ch / 2, cr));
        crowns.setMatrixAt(k, m4);
        col.setHSL(0.25 + rnd * 0.06, 0.36 + rnd * 0.1, 0.22 + rnd * 0.09);     // summer hardwood greens
      } else {
        const tr = Math.max(0.12, h * 0.012);
        m4.compose(v3(x, y, gz), q, new THREE.Vector3(tr, h * 0.25, tr));
        trunks.setMatrixAt(k, m4);
        m4.compose(v3(x, y, gz + h * 0.12), q, new THREE.Vector3(r, h * 0.88, r));
        crowns.setMatrixAt(k, m4);
        col.setHSL(0.3 + rnd * 0.04, 0.28, 0.24 + rnd * 0.06);
      }
      crowns.setColorAt(k, col);
    });
    for (const im of [trunks, crowns]) {
      im.geometry = im.geometry.clone();
      im.geometry.setAttribute('aTop', new THREE.InstancedBufferAttribute(tops, 1));
    }
    trunks.castShadow = true;
    crowns.castShadow = true;
    crowns.receiveShadow = true;
    crowns.userData.baseColors = crowns.instanceColor.array.slice();
    crowns.userData.deciduous = kind === 0;
    g.add(trunks, crowns);
  });
  // understory: inside woodland (three or more neighbours within 9 m) the gaps between the stems are closed by
  // shrubs and saplings, so a wood reads as a wall of green from the lawn, as it does in life
  {
    const cellSz = 9, grid = new Map();
    const key = (a, b) => a + ',' + b;
    for (let i = 0; i < n; i++) {
      const k = key(Math.floor(data[i * 6] / cellSz), Math.floor(data[i * 6 + 1] / cellSz));
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(i);
    }
    const bushes = [];
    for (let i = 0; i < n; i++) {
      if (data[i * 6 + 5] >= 0.1) continue;                    // conifers, and open-grown trees on lawn or by paving
      const x = data[i * 6], y = data[i * 6 + 1], gx = Math.floor(x / cellSz), gy = Math.floor(y / cellSz);
      let near = 0;
      for (let a = gx - 1; a <= gx + 1; a++) for (let b = gy - 1; b <= gy + 1; b++) {
        for (const j of grid.get(key(a, b)) || []) if (j !== i && (data[j * 6] - x) ** 2 + (data[j * 6 + 1] - y) ** 2 < 81) near++;
      }
      if (near < 3) continue;
      const r1 = fract(Math.sin(i * 78.233) * 43758.5453), r2 = fract(Math.sin(i * 39.425) * 24634.6345), r3 = fract(Math.sin(i * 11.135) * 96321.9124);
      bushes.push([x + (r1 - 0.5) * 4.4, y + (r2 - 0.5) * 4.4, data[i * 6 + 2], 1.7 + r3 * 1.5, 1.5 + r1 * 1.4, data[i * 6 + 2] + data[i * 6 + 3], r2]);
      if (r3 > 0.45) bushes.push([x - (r2 - 0.5) * 5.0, y + (r3 - 0.5) * 5.0, data[i * 6 + 2], 1.5 + r1 * 1.3, 1.3 + r2 * 1.2, data[i * 6 + 2] + data[i * 6 + 3], r1]);
    }
    if (bushes.length) {
      const bm = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 1, flatShading: true });
      cutaway(bm);
      const im = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), bm, bushes.length);
      const tops = new Float32Array(bushes.length);
      bushes.forEach(([x, y, gz, rb, hb, top, rnd], k) => {
        q.setFromAxisAngle(up, rnd * 6.283);
        m4.compose(v3(x, y, gz + hb * 0.75), q, new THREE.Vector3(rb, hb, rb));
        im.setMatrixAt(k, m4);
        col.setHSL(0.26 + rnd * 0.05, 0.36 + rnd * 0.08, 0.17 + rnd * 0.07);
        im.setColorAt(k, col);
        tops[k] = top - z0;
      });
      im.geometry.setAttribute('aTop', new THREE.InstancedBufferAttribute(tops, 1));
      im.castShadow = false;
      im.receiveShadow = true;
      im.userData.baseColors = im.instanceColor.array.slice();
      im.userData.deciduous = true;
      im.userData.understory = true;
      g.add(im);
    }
  }
  // canopy mask over the terrain's extent: where crowns stand, the ground is drawn as shaded woodland floor in leaf
  const T = M.terrain;
  const wX = (T.nx - 1) * T.cell, wY = (T.ny - 1) * T.cell;
  const cw = 768, chh = Math.max(2, Math.round((cw * wY) / wX));
  const cv = document.createElement('canvas');
  cv.width = cw; cv.height = chh;
  const cx = cv.getContext('2d');
  cx.fillStyle = '#000'; cx.fillRect(0, 0, cw, chh);
  cx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < n; i++) {
    const [x, y, , , r] = data.subarray(i * 6, i * 6 + 5);
    const px = ((x - T.x0) / wX) * cw, py = (1 - (y - T.y0) / wY) * chh, pr = Math.max(((r * 1.35) / wX) * cw, 1.5);
    const gr = cx.createRadialGradient(px, py, pr * 0.45, px, py, pr);
    gr.addColorStop(0, 'rgba(255,255,255,0.9)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    cx.fillStyle = gr; cx.beginPath(); cx.arc(px, py, pr, 0, 6.2832); cx.fill();
  }
  const ct = new THREE.CanvasTexture(cv);
  ct.colorSpace = THREE.NoColorSpace;
  terrainUniforms.uCanopy.value = ct;
}
const fract = (v) => v - Math.floor(v);

// Trees that stand between the camera and what it is looking at are dropped, so the
// woods never wall off the house at eye level. Their shadows stay.
const cut = { a: { value: new THREE.Vector3() }, b: { value: new THREE.Vector3() }, k: { value: 0 } };
function cutaway(mat) {
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uCutA = cut.a;
    shader.uniforms.uCutB = cut.b;
    shader.uniforms.uCutK = cut.k;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nuniform vec3 uCutA;\nuniform vec3 uCutB;\nuniform float uCutK;\nattribute float aTop;')
      .replace('#include <project_vertex>', `#include <project_vertex>
        #ifdef USE_INSTANCING
        {
          vec2 io = (modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xz;
          vec2 ab = uCutB.xz - uCutA.xz;
          float tt = clamp(dot(io - uCutA.xz, ab) / max(dot(ab, ab), 1e-4), 0.0, 1.0);
          float dd = distance(io, uCutA.xz + ab * tt);
          float sight = mix(uCutA.y, uCutB.y, tt);
          float corridor = mix(9.0, 17.0, tt) * uCutK;
          if (dd < corridor && sight < aTop + 2.5) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
        }
        #endif`);
  };
}
function updateCutaway() {
  cut.a.value.copy(camera.position);
  cut.b.value.copy(controls.target);
  const d = camera.position.clone().sub(controls.target);
  const pitch = Math.atan2(d.y, Math.hypot(d.x, d.z)) * (180 / Math.PI);
  cut.k.value = state.exact ? 0 : 1 - THREE.MathUtils.smoothstep(pitch, 32, 55);      // a photo-matched view keeps every tree the photograph has
}

// --------------------------------------------------------------- models -----
const clayMat = new THREE.MeshStandardMaterial({ color: 0xdcd8ce, roughness: 0.95 });
const clayGlass = new THREE.MeshStandardMaterial({ color: 0x9aa4ab, roughness: 0.35 });
const clayFlat = new THREE.MeshStandardMaterial({ color: 0xc4c0b5, roughness: 1, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
const evidenceMats = {};

async function loadModel(m) {
  const gltf = await new GLTFLoader().loadAsync(`${ASSETS}/${m.file}`);
  const root = gltf.scene;
  root.name = m.id;
  root.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = true;
    o.receiveShadow = true;
    const mat = o.material;
    const isGlass = /glass/i.test(mat.name);
    if (isGlass) {
      mat.color.set(0x4a5863); mat.roughness = 0.1; mat.metalness = 0.0; mat.envMapIntensity = 1.2;   // slate panes: black frames and mullions read against them
      mat.emissive.set(0x232d35);                                 // the sky they mirror: panes never go black in shade
      o.castShadow = false;
    }
    if (mat.map) { mat.map.anisotropy = 8; }
    if (m.layer === 'sitework' && !/fence/.test(o.name)) {          // paving lies on the ground
      o.castShadow = false;
      mat.polygonOffset = true; mat.polygonOffsetFactor = -2; mat.polygonOffsetUnits = -2;
      o.userData.flat = true;
    }
    o.userData.photo = mat;
    o.userData.glass = isGlass;
    o.userData.model = m.id;
    const info = (M.parts && M.parts[o.name]) || {};
    o.userData.info = { ...o.userData, ...info };
    modelMeshes.push(o);
  });
  groups[m.id] = root;
  scene.add(root);
}

// --------------------------------------------------------------- parcel -----
function drapedRibbon(points, width, lift, color, opacity = 1) {
  // points: [[x, y], ...] in site frame; follows the terrain
  const pos = [];
  const idx = [];
  const samples = [];
  for (let i = 0; i < points.length - 1; i++) {
    const [ax, ay] = points[i], [bx, by] = points[i + 1];
    const len = Math.hypot(bx - ax, by - ay);
    const steps = Math.max(1, Math.ceil(len / 1.5));
    for (let s = 0; s < steps; s++) samples.push([ax + ((bx - ax) * s) / steps, ay + ((by - ay) * s) / steps]);
  }
  samples.push(points[points.length - 1]);
  samples.forEach(([x, y], i) => {
    const p = samples[Math.max(i - 1, 0)], n = samples[Math.min(i + 1, samples.length - 1)];
    let dx = n[0] - p[0], dy = n[1] - p[1];
    const l = Math.hypot(dx, dy) || 1;
    dx /= l; dy /= l;
    const ox = (-dy * width) / 2, oy = (dx * width) / 2;
    const z = groundZ(x, y) + lift;
    const a = v3(x + ox, y + oy, z), b = v3(x - ox, y - oy, z);
    pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
    if (i < samples.length - 1) { const k = i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  const mat = new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4, toneMapped: false });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 2;
  return mesh;
}

function buildParcel() {
  const g = new THREE.Group();
  groups.parcel = g;
  scene.add(g);
  if (!M.parcel) return;
  const ring = M.parcel.slice();
  if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) ring.push(ring[0]);
  g.add(drapedRibbon(ring, 0.55, 0.18, ACCENT));
  // corner pins
  const pinGeo = new THREE.CylinderGeometry(0.22, 0.22, 3.2, 8).translate(0, 1.6, 0);
  const pinMat = new THREE.MeshBasicMaterial({ color: ACCENT, toneMapped: false });
  for (const [x, y] of (M.parcel_corners || [])) {
    const pin = new THREE.Mesh(pinGeo, pinMat);
    pin.position.copy(v3(x, y, groundZ(x, y)));
    g.add(pin);
  }
}

// ----------------------------------------------------------------- tags -----
function addTag(text, pos, layer, cls = '', small = '') {
  const el = document.createElement('div');
  el.className = `tag ${cls}`.trim();
  el.textContent = text;
  if (small) { const s = document.createElement('small'); s.textContent = small; el.append(s); }
  $('labels').append(el);
  const t = { el, pos, layer, visible: true };
  tags.push(t);
  return t;
}

function buildTags() {
  for (const l of M.labels || []) {
    const z = l.at.length > 2 ? l.at[2] : groundZ(l.at[0], l.at[1]) + (l.lift ?? 9);
    const t = addTag(l.text, v3(l.at[0], l.at[1], z), 'labels', l.kind || '', l.note || '');
    if (l.since) t.since = l.since;
  }
}

function buildDimensions() {
  const g = new THREE.Group();
  groups.dimensions = g;
  scene.add(g);
  const pts = [];
  for (const d of M.dimensions || []) {
    const a = v3(...d.a), b = v3(...d.b);
    pts.push(a, b);
    // end ticks
    const dir = b.clone().sub(a).normalize();
    const side = new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(0.35);
    const upv = new THREE.Vector3(0, 0.35, 0);
    for (const p of [a, b]) {
      pts.push(p.clone().sub(side), p.clone().add(side));
      pts.push(p.clone().sub(upv), p.clone().add(upv));
    }
    addTag(d.text, a.clone().add(b).multiplyScalar(0.5), 'dimensions', 'dim');
  }
  if (pts.length) {
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: INK, depthTest: false, transparent: true, toneMapped: false }));
    lines.renderOrder = 5;
    g.add(lines);
  }
}

const tmpV = new THREE.Vector3();
function layoutTags() {
  const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
  for (const t of tags) {
    const on = t.visible && state.layers[t.layer] !== false;
    if (!on) { t.el.style.display = 'none'; continue; }
    tmpV.copy(t.pos).project(camera);
    if (tmpV.z > 1 || tmpV.z < -1 || Math.abs(tmpV.x) > 1.1 || Math.abs(tmpV.y) > 1.1) { t.el.style.display = 'none'; continue; }
    const x = (tmpV.x * 0.5 + 0.5) * w, y = (-tmpV.y * 0.5 + 0.5) * h;
    t.el.style.display = '';
    const anchorBottom = !t.el.classList.contains('dim') && !t.el.classList.contains('measure') && !t.el.classList.contains('road');
    t.el.style.transform = anchorBottom
      ? `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, calc(-100% - 14px))`
      : `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -50%)`;
  }
}

// ---------------------------------------------------------- LiDAR points ----
const RAMP = [new THREE.Color(0x1f8a7a), new THREE.Color(0xf2c14e), new THREE.Color(0xe4572e)];
let pointsPromise = null;
function loadPoints() {
  if (pointsPromise) return pointsPromise;
  pointsPromise = (async () => {
    const p = M.points;
    const buf = await fetchBuffer(`${ASSETS}/${p.file}`);
    const u16 = new Uint16Array(buf);
    const u8 = new Uint8Array(buf);
    const n = p.count;
    const pos = new Float32Array(n * 3);
    const colr = new Float32Array(n * 3);
    const c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const x = p.origin[0] + u16[i * 4] * p.scale[0];
      const y = p.origin[1] + u16[i * 4 + 1] * p.scale[1];
      const z = p.origin[2] + u16[i * 4 + 2] * p.scale[2];
      const cls = u8[i * 8 + 6];
      pos[i * 3] = x; pos[i * 3 + 1] = z - z0; pos[i * 3 + 2] = -y;
      if (cls === 2) {
        c.setRGB(0.5, 0.46, 0.4);
      } else {
        const t = Math.min(Math.max(z - groundZ(x, y), 0) / 24, 1);
        c.copy(RAMP[0]).lerp(RAMP[1], Math.min(t * 2, 1));
        if (t > 0.5) c.lerp(RAMP[2], (t - 0.5) * 2);
      }
      colr[i * 3] = c.r; colr[i * 3 + 1] = c.g; colr[i * 3 + 2] = c.b;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colr, 3));
    const mat = new THREE.PointsMaterial({ size: 2.6, vertexColors: true, sizeAttenuation: false, toneMapped: false });
    const pts = new THREE.Points(geo, mat);
    pts.name = 'lidar';
    groups.points = pts;
    pts.visible = state.layers.points === true;
    scene.add(pts);
    state.dirty = true;
  })();
  return pointsPromise;
}

// --------------------------------------------------------------- layers -----
function setupLayers() {
  const ul = $('layers');
  for (const l of M.layers) {
    state.layers[l.id] = l.on !== false;
    const li = document.createElement('li');
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = state.layers[l.id];
    input.dataset.layer = l.id;
    input.addEventListener('change', () => setLayer(l.id, input.checked));
    const span = document.createElement('span');
    span.textContent = l.label;
    label.append(input, span);
    li.append(label);
    if (l.note) { const s = document.createElement('small'); s.textContent = l.note; li.append(s); }
    ul.append(li);
    setLayer(l.id, state.layers[l.id], true);
  }
}

function setLayer(id, on, quiet = false) {
  state.layers[id] = on;
  const box = document.querySelector(`input[data-layer="${id}"]`);
  if (box) box.checked = on;
  if (id === 'points') {
    if (on) loadPoints().then(() => { groups.points.visible = state.layers.points; state.dirty = true; });
    else if (groups.points) groups.points.visible = false;
  } else if (id === 'aerial') {
    applyTerrainLook();
  } else if (id === 'contours') {
    applyTerrainLook();
  } else if (M.models.some((m) => (m.layer || m.id) === id)) {
    syncModels();
  } else if (groups[id]) {
    groups[id].visible = on;
  }
  if (!quiet) state.dirty = true;
  state.dirty = true;
}

function applyTerrainLook() {
  if (!terrainMat) return;
  const photo = state.mode === 'photo' && state.layers.aerial !== false && orthoTex;
  terrainMat.map = photo ? orthoTex : null;
  terrainMat.color.set(photo ? 0xffffff : 0xd6d2c7);
  terrainMat.needsUpdate = true;
  terrainUniforms.uContour.value = state.layers.contours ? 1 : 0;
}

// ---------------------------------------------------------------- modes -----
const MODE_HINT = {
  photo: () => `True materials and colours, on the ${state.year ?? ''} aerial photograph.`.replace('  ', ' '),
  clay: () => 'Plain massing model with 1 ft contours: shape and shadow only.',
  evidence: () => 'Coloured by how each part is known. Click any part for its source.',
};

function setupModes() {
  for (const b of $('mode').querySelectorAll('button')) b.addEventListener('click', () => applyMode(b.dataset.mode));
  const ul = $('evidence-legend');
  for (const [key, e] of Object.entries(M.evidence || {})) {
    evidenceMats[key] = new THREE.MeshStandardMaterial({ color: e.color, roughness: 0.9 });
    const li = document.createElement('li');
    li.innerHTML = '<i></i><span><b></b> — <span></span></span>';
    li.querySelector('i').style.background = e.color;
    li.querySelector('b').textContent = e.label;
    li.querySelector('span span').textContent = e.detail;
    ul.append(li);
  }
  evidenceMats._none = new THREE.MeshStandardMaterial({ color: 0xcfcbc0, roughness: 0.9 });
}

function applyMode(mode) {
  state.mode = mode;
  for (const b of $('mode').querySelectorAll('button')) b.setAttribute('aria-checked', String(b.dataset.mode === mode));
  $('mode-hint').textContent = MODE_HINT[mode]();
  $('evidence-legend').hidden = mode !== 'evidence';
  for (const o of modelMeshes) o.material = materialFor(o);
  if (mode !== 'photo' && state.layers.contours === false && !state._contourTouched) setLayer('contours', true, true);
  if (mode === 'photo' && !state._contourTouched) setLayer('contours', false, true);
  applyTerrainLook();
  tintTrees(mode);
  terrainUniforms.uSummer.value = mode === 'photo' ? (state.leaf ?? 0) : 0;
  if (state.selected) highlight(state.selected);
  state.dirty = true;
}

function materialFor(o) {
  if (state.mode === 'photo') return o.userData.photo;
  if (state.mode === 'clay') return o.userData.glass ? clayGlass : o.userData.flat ? clayFlat : clayMat;
  return evidenceMats[o.userData.info.evidence] || evidenceMats._none;
}

const TWIG = new THREE.Color(0x857b70);
function tintTrees(mode) {
  const g = groups.trees;
  if (!g) return;
  const c = new THREE.Color();
  g.traverse((o) => {
    if (!o.isInstancedMesh || !o.userData.baseColors) return;
    const base = o.userData.baseColors;
    const bare = o.userData.deciduous ? 1 - (state.leaf ?? 1) : 0;
    for (let i = 0; i < o.count; i++) {
      if (mode === 'photo') { c.fromArray(base, i * 3); if (bare > 0) c.lerp(TWIG, bare); } else c.setRGB(0.8, 0.79, 0.74);
      o.setColorAt(i, c);
    }
    o.instanceColor.needsUpdate = true;
  });
}

// ---------------------------------------------------------------- views -----
let tween = null;
function setupViews() {
  const row = $('views');
  M.views.filter((v) => !v.hidden).forEach((v) => {
    const b = document.createElement('button');
    b.textContent = v.label;
    b.title = v.title || v.label;
    b.addEventListener('click', () => goToView(v));
    row.append(b);
  });
}

function goToView(v, animate = true) {
  const pos = v3(...v.pos), target = v3(...v.target);
  // keep the subject clear of the side panels on wide screens / the sheet on phones
  const aspect = window.innerWidth / window.innerHeight;
  const scale = v.fov ? 1 : aspect < 0.8 ? 1.9 : aspect < 1.25 ? 1.35 : 1;       // photo-matched views are exact
  pos.sub(target).multiplyScalar(scale).add(target);
  const fov = v.fov || 38;
  if (camera.fov !== fov) { camera.fov = fov; camera.updateProjectionMatrix(); }
  // photo-matched cameras may look upward from eye level and stand close; normal views stay above the ground
  controls.maxPolarAngle = v.fov ? Math.PI * 0.98 : Math.PI * 0.495;
  controls.minDistance = v.fov ? 0.5 : 6;
  state.roll = (v.roll || 0) * Math.PI / 180;                  // a hand-held photograph is rarely level
  state.exact = !!v.fov;
  const oc = v.overcast ? 1 : 0;
  if ((state.overcast || 0) !== oc) { state.overcast = oc; if (sun) setSun(); }
  if (!animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    camera.position.copy(pos);
    controls.target.copy(target);
    controls.update();
    state.dirty = true;
    return;
  }
  tween = { t0: performance.now(), dur: 1100, p0: camera.position.clone(), p1: pos, q0: controls.target.clone(), q1: target };
}

function stepTween(now) {
  if (!tween) return;
  const k = Math.min((now - tween.t0) / tween.dur, 1);
  const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  camera.position.lerpVectors(tween.p0, tween.p1, e);
  controls.target.lerpVectors(tween.q0, tween.q1, e);
  if (k >= 1) tween = null;
  state.dirty = true;
}

// ------------------------------------------------------------------ sun -----
function setupSun() {
  const time = $('time'), date = $('date');
  time.value = state.minutes;
  date.value = state.day;
  time.addEventListener('input', () => { state.minutes = +time.value; stopPlay(); setSun(); });
  date.addEventListener('input', () => { state.day = +date.value; setSun(); });
  for (const b of document.querySelectorAll('[data-day]')) {
    b.addEventListener('click', () => { state.day = +b.dataset.day; date.value = state.day; setSun(); });
  }
  $('sun-play').addEventListener('click', () => (state.playing ? stopPlay() : startPlay()));
  setSun();
}
function startPlay() {
  const { rise } = daylight(YEAR, state.day, M.location.lat, M.location.lon);
  if (state.minutes >= 1290) state.minutes = (rise ?? 360) - 20;
  state.playing = true;
  $('sun-play').setAttribute('aria-pressed', 'true');
  $('sun-play').textContent = 'Pause';
}
function stopPlay() {
  state.playing = false;
  $('sun-play').setAttribute('aria-pressed', 'false');
  $('sun-play').textContent = 'Play day';
}

// Hardwoods are bare from about late October to late April: thin the crowns and let the
// low winter sun through instead of casting solid shadows.
function setSeason() {
  const leaf = THREE.MathUtils.smoothstep(state.day, 105, 135) * (1 - THREE.MathUtils.smoothstep(state.day, 285, 310));
  groups.trees?.traverse((o) => {
    if (!o.isInstancedMesh || !o.userData.deciduous) return;
    const full = leaf > 0.5;                                   // in leaf the woods are solid; bare, a faint haze of twigs
    if (o.material.transparent === full) { o.material.transparent = !full; o.material.depthWrite = full; o.material.needsUpdate = true; }
    o.material.opacity = full ? 1 : 0.07 + 0.6 * leaf;
    if (o.userData.understory) o.visible = leaf > 0.3;          // bare brush is all but invisible; do not draw a grey fog for it
    o.castShadow = full && !o.userData.understory;
  });
  if (state.leaf !== leaf) { state.leaf = leaf; tintTrees(state.mode); }
  terrainUniforms.uSummer.value = state.mode === 'photo' ? leaf : 0;
  state.dirty = true;
}

const cDay = new THREE.Color(0xfff6e8), cLow = new THREE.Color(0xffb46b), cTmp = new THREE.Color();
function setSun() {
  const { lat, lon } = M.location;
  const { date } = easternToUTC(YEAR, state.day, state.minutes);
  const s = sunPosition(date, lat, lon);
  const az = s.az - (M.location.grid_convergence_deg || 0) * (Math.PI / 180);   // true north -> grid north
  const alt = s.alt;
  const altDeg = (alt * 180) / Math.PI;
  const dir = new THREE.Vector3(Math.sin(az) * Math.cos(alt), Math.sin(alt), -Math.cos(az) * Math.cos(alt));
  const focus = v3(...(M.focus || [0, 12, z0]));
  sun.target.position.copy(focus);
  const up = Math.max(altDeg, 0.5);
  const lightDir = altDeg > 0.5 ? dir.clone() : new THREE.Vector3(dir.x, Math.sin(0.5 * Math.PI / 180), dir.z).normalize();
  // under cloud the light comes from the whole sky: a weak, nearly overhead key keeps the shade under decks and eaves
  const ocNow = state.overcast || 0;
  if (ocNow > 0) lightDir.set(lightDir.x * 0.18, 1, lightDir.z * 0.18).normalize();
  sun.position.copy(focus).addScaledVector(lightDir, 320);
  const day = THREE.MathUtils.smoothstep(altDeg, -4, 8);
  const high = THREE.MathUtils.smoothstep(altDeg, 2, 28);
  // open shade under a real sky is only two or three stops below full sun, and a white wall stays white in it;
  // under cloud (a photo-matched view may say "overcast") the sun all but goes and the sky does the lighting
  const oc = state.overcast || 0;
  sun.intensity = 2.5 * day * (1 - 0.62 * oc);
  sun.color.copy(cTmp.copy(cLow).lerp(cDay, oc > 0 ? 1 : high));
  sun.castShadow = altDeg > 0.6;
  hemi.intensity = (0.3 + 0.95 * day + 0.25 * day * (1 - high)) * (1 + 0.7 * oc);
  hemi.color.setHSL(0.58, 0.35 * (1 - 0.6 * oc), 0.55 + 0.33 * day);
  fill.position.copy(focus).add(new THREE.Vector3(-dir.x, 0.6, -dir.z).multiplyScalar(200));
  fill.intensity = (0.2 + 0.4 * day) * (1 + 0.5 * oc);
  scene.environmentIntensity = 0.12 + 0.3 * day;
  renderer.toneMappingExposure = 0.8 + 0.2 * day;
  // backdrop
  const top = new THREE.Color().setHSL(0.57, 0.3, 0.86).lerp(new THREE.Color(0x232c38), 1 - day);
  const bottom = new THREE.Color(0xf1eee6).lerp(new THREE.Color(0xf3c9a0), (1 - high) * day).lerp(new THREE.Color(0x39424e), 1 - day);
  document.documentElement.style.setProperty('--sky-top', `#${top.getHexString()}`);
  document.documentElement.style.setProperty('--sky-bottom', `#${bottom.getHexString()}`);
  setSeason();
  $('time-out').textContent = formatClock(state.minutes);
  $('date-out').textContent = formatDay(YEAR, state.day);
  $('sun-readout').textContent = altDeg > 0
    ? `${compassPoint(s.az)} ${Math.round((s.az * 180) / Math.PI)}° · ${Math.round(up)}° high`
    : 'below horizon';
  $('time').value = state.minutes;
  state.dirty = true;
}

// -------------------------------------------------------------- picking -----
const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let highlightMat = null;

function pointerNDC(e) {
  const r = renderer.domElement.getBoundingClientRect();
  ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
}

function setupPicking() {
  const el = renderer.domElement;
  let down = null;
  el.addEventListener('pointerdown', (e) => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; });
  el.addEventListener('pointerup', (e) => {
    if (!down) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    const quick = performance.now() - down.t < 600;
    down = null;
    if (moved > 6 || !quick) return;
    pointerNDC(e);
    ray.setFromCamera(ndc, camera);
    if (state.measuring) { measureClick(); return; }
    const visible = modelMeshes.filter((o) => groups[o.userData.model]?.visible !== false);
    const hit = ray.intersectObjects(visible, false)[0];
    if (hit) { select(hit.object); return; }
    const ground = ray.intersectObject(terrain, false)[0];
    select(null);
    if (ground) probeGround(ground.point);
  });
  let hoverRaf = 0;
  el.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse' || e.buttons || state.measuring || hoverRaf) return;
    hoverRaf = requestAnimationFrame(() => {
      hoverRaf = 0;
      pointerNDC(e);
      ray.setFromCamera(ndc, camera);
      const visible = modelMeshes.filter((o) => groups[o.userData.model]?.visible !== false);
      el.style.cursor = ray.intersectObjects(visible, false).length ? 'pointer' : '';
    });
  });
  $('card-close').addEventListener('click', () => select(null));
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') { select(null); if (state.measuring) toggleMeasure(false); } });
}

function select(o) {
  if (state.selected) state.selected.material = materialFor(state.selected);
  state.selected = o;
  const card = $('card');
  if (probePin) probePin.visible = false;
  if (!o) { card.hidden = true; state.dirty = true; return; }
  highlight(o);
  const info = o.userData.info || {};
  const ev = (M.evidence || {})[info.evidence];
  $('card-kind').textContent = ev ? ev.label : 'Model part';
  $('card-title').textContent = info.label || prettify(o.name);
  $('card-desc').textContent = info.desc || '';
  const dl = $('card-dl');
  dl.textContent = '';
  const rows = [];
  if (info.dims) rows.push(['Size', info.dims]);
  if (info.material) rows.push(['Finish', info.material]);
  if (info.source) rows.push(['Source', info.source]);
  if (info.confidence) rows.push(['Confidence', info.confidence]);
  for (const [k, v] of rows) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.append(dt, dd);
  }
  card.hidden = false;
  state.dirty = true;
}

// Clicking bare ground reports its surveyed elevation and position.
let probePin = null;
function probeGround(pt) {
  const x = pt.x, y = -pt.z, z = pt.y + z0;
  const g = ((M.location.grid_convergence_deg || 0) * Math.PI) / 180;
  const north = -x * Math.sin(g) + y * Math.cos(g), east = x * Math.cos(g) + y * Math.sin(g);
  const lat = M.location.lat + north / 111070;
  const lon = M.location.lon + east / (111320 * Math.cos((M.location.lat * Math.PI) / 180));
  const rel = z - z0;
  $('card-kind').textContent = 'Ground · 2017 LiDAR';
  $('card-title').textContent = `${(z / FT).toFixed(1)} ft above sea level`;
  $('card-desc').textContent = `${Math.abs(rel / FT).toFixed(1)} ft ${rel >= 0 ? 'above' : 'below'} the grade at the front door.`;
  const dl = $('card-dl');
  dl.textContent = '';
  for (const [k, v] of [['Elevation', `${z.toFixed(2)} m NAVD88`], ['Position', `${lat.toFixed(5)}° N, ${Math.abs(lon).toFixed(5)}° W`]]) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.append(dt, dd);
  }
  if (!probePin) {
    probePin = new THREE.Mesh(new THREE.ConeGeometry(0.35, 1.6, 10).rotateX(Math.PI).translate(0, 0.8, 0),
      new THREE.MeshBasicMaterial({ color: ACCENT, toneMapped: false }));
    scene.add(probePin);
  }
  probePin.position.copy(pt);
  probePin.visible = true;
  $('card').hidden = false;
  state.dirty = true;
}

function highlight(o) {
  const base = materialFor(o);
  highlightMat?.dispose();
  highlightMat = base.clone();
  highlightMat.emissive = new THREE.Color(ACCENT);
  highlightMat.emissiveIntensity = 0.42;
  o.material = highlightMat;
}

const prettify = (s) => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

// -------------------------------------------------------------- measure -----
let measureLine = null;
let measureTag = null;
function setupMeasure() {
  $('measure').addEventListener('click', () => toggleMeasure(!state.measuring));
  $('measure-clear').addEventListener('click', clearMeasure);
}
function toggleMeasure(on) {
  state.measuring = on;
  $('measure').setAttribute('aria-pressed', String(on));
  renderer.domElement.classList.toggle('measuring', on);
  $('measure-hint').textContent = on ? 'Click the first point…' : 'Click two points on the model or ground.';
  if (on) select(null);
}
function clearMeasure() {
  state.measure = [];
  if (measureLine) { scene.remove(measureLine); measureLine.geometry.dispose(); measureLine = null; }
  if (measureTag) { measureTag.el.remove(); tags.splice(tags.indexOf(measureTag), 1); measureTag = null; }
  $('measure-clear').hidden = true;
  state.dirty = true;
}
function measureClick() {
  const targets = [terrain, ...modelMeshes.filter((o) => groups[o.userData.model]?.visible !== false)];
  const hit = ray.intersectObjects(targets, false)[0];
  if (!hit) return;
  if (state.measure.length >= 2) clearMeasure();
  state.measure.push(hit.point.clone());
  if (state.measure.length === 1) {
    $('measure-hint').textContent = 'Click the second point…';
    const dot = new THREE.BufferGeometry().setFromPoints([hit.point, hit.point]);
    measureLine = new THREE.LineSegments(dot, new THREE.LineBasicMaterial({ color: ACCENT, depthTest: false, toneMapped: false }));
    measureLine.renderOrder = 9;
    scene.add(measureLine);
  } else {
    const [a, b] = state.measure;
    measureLine.geometry.setFromPoints([a, b]);
    const d = a.distanceTo(b);
    const run = Math.hypot(b.x - a.x, b.z - a.z);
    const rise = Math.abs(b.y - a.y);
    measureTag = addTag(`${feet(d)}  ·  ${d.toFixed(2)} m`, a.clone().add(b).multiplyScalar(0.5), 'measure', 'measure');
    $('measure-hint').textContent = `Distance ${feet(d)} (${d.toFixed(2)} m) — run ${feet(run)}, rise ${feet(rise)}.`;
    $('measure-clear').hidden = false;
  }
  state.dirty = true;
}
function feet(m) {
  const total = Math.round((m / FT) * 12);
  const ft = Math.floor(total / 12), inch = total % 12;
  return `${ft}′ ${inch}″`;
}

// ---------------------------------------------------------------- about -----
function setupAbout() {
  const body = $('about-body');
  const a = M.about || {};
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let html = `<p class="eyebrow">Sources &amp; method</p><h2>${esc(a.heading || 'How this model was made')}</h2>`;
  for (const p of a.intro || []) html += `<p>${esc(p)}</p>`;
  if (a.accuracy?.length) {
    html += '<h3>How close is it</h3><table><thead><tr><th>Check</th><th>Result</th><th>Basis</th></tr></thead><tbody>';
    for (const r of a.accuracy) html += `<tr><td>${esc(r.check)}</td><td class="num">${esc(r.result)}</td><td>${esc(r.basis)}</td></tr>`;
    html += '</tbody></table>';
  }
  if (a.sources?.length) {
    html += '<h3>Data sources</h3><table><thead><tr><th>Source</th><th>Used for</th><th>Date</th></tr></thead><tbody>';
    for (const s of a.sources) {
      const name = s.url ? `<a href="${esc(s.url)}" rel="noopener" target="_blank">${esc(s.name)}</a>` : esc(s.name);
      html += `<tr><td>${name}${s.license ? `<br><code>${esc(s.license)}</code>` : ''}</td><td>${esc(s.used_for)}</td><td class="num">${esc(s.date || '')}</td></tr>`;
    }
    html += '</tbody></table>';
  }
  if (a.limits?.length) {
    html += '<h3>What is inferred, not measured</h3><ul>';
    for (const l of a.limits) html += `<li>${esc(l)}</li>`;
    html += '</ul>';
  }
  if (a.record?.length) {
    html += '<h3>County record</h3><table><tbody>';
    for (const r of a.record) html += `<tr><td>${esc(r.k)}</td><td class="num">${esc(r.v)}</td></tr>`;
    html += '</tbody></table>';
  }
  if (a.footer) html += `<h3>Notes</h3><p>${esc(a.footer)}</p>`;
  body.innerHTML = html;
  $('open-about').addEventListener('click', () => $('about').showModal());
  $('about').addEventListener('click', (e) => { if (e.target === $('about')) $('about').close(); });
}

// ----------------------------------------------------------------- loop -----
function onResize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  state.dirty = true;
}

let lastT = 0;
const camDir = new THREE.Vector3();
function tick(now) {
  const dt = Math.min((now - lastT) / 1000, 0.1);
  lastT = now;
  stepTween(now);
  if (state.playing) {
    state.minutes += dt * 48;                       // a day in about 20 s
    if (state.minutes > 1300) { state.minutes = 1300; stopPlay(); }
    setSun();
  }
  controls.update();
  if (state.roll) { camera.rotateZ(state.roll); camera.updateMatrixWorld(); }   // orbit controls re-level the camera every frame
  terrainUniforms.uCanopyOn.value = groups.trees && groups.trees.visible ? 1 : 0;
  if (!state.dirty) return;
  state.dirty = false;
  renderOnce();
}

function renderOnce() {
  updateCutaway();
  renderer.render(scene, camera);
  layoutTags();
  camera.getWorldDirection(camDir);
  const heading = Math.atan2(camDir.x, -camDir.z);
  $('compass-needle').setAttribute('transform', `rotate(${(-heading * 180) / Math.PI})`);
}
