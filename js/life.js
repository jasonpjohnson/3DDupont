// Life mode: a small household living on the lot. People have needs (hunger, energy, hygiene, bladder,
// fun, social), walk the real grounds on a grid built from the model (tools/build_nav.py), and use the
// places the property actually has: the house (through its real doors), the hot tub, the fire pit on the
// walk-out patio, the porch, a grill on the rear deck, the vegetable cage, the berry arbors, the
// greenhouse, the pole barn, the detached garage, the lawn - and the car, to go to work.
// The inside of the house is not modelled: people go in through a door and come back out of it.
import * as THREE from 'three';

let V = null;          // the viewer (window.__viewer)
let G = null;          // the running game

export async function start(viewer) {
  V = viewer;
  if (!G) {
    G = new Game();
    await G.init();
  }
  G.enter();
}

// --------------------------------------------------------------- helpers ----
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const money = (n) => `${n < 0 ? '−' : ''}$${Math.abs(Math.round(n)).toLocaleString('en-US')}`;
const SAVE_KEY = 'dupont-life-v1';
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const NEEDS = [
  ['hunger', 'Hunger', '🍽️'], ['energy', 'Energy', '⚡'], ['hygiene', 'Hygiene', '🧼'],
  ['bladder', 'Bladder', '🚻'], ['fun', 'Fun', '🎈'], ['social', 'Social', '💬'],
];
const SKILLS = [['cooking', 'Cooking'], ['gardening', 'Gardening'], ['handiness', 'Handiness'], ['fitness', 'Fitness']];
const TRAITS = {
  greenthumb: { label: 'Green thumb', hint: 'loves the garden and greenhouse' },
  handy: { label: 'Handy', hint: 'happiest in the barn or the garage' },
  foodie: { label: 'Foodie', hint: 'cooking and eating are the best part of the day' },
  social: { label: 'Social butterfly', hint: 'needs company more often, gets more from it' },
  outdoorsy: { label: 'Outdoorsy', hint: 'hot tub, fire pit, lawn - anywhere outside' },
  nightowl: { label: 'Night owl', hint: 'stays up late, fun at night counts double' },
};
const SHIRTS = ['#e4572e', '#2e86ab', '#3d9970', '#f2c14e', '#8e5ea2', '#d1495b', '#1c2630', '#f7f4ec'];
const SKINS = ['#f1c9a5', '#e0ac7e', '#c68a5e', '#8d5a3b', '#5c3a24'];
const HAIRS = ['#2b1d14', '#6b4226', '#b5763c', '#d9b26a', '#9a9a9a', '#1a1a1a'];
const NAMES = ['Avery', 'Jordan', 'Riley', 'Morgan', 'Casey', 'Quinn', 'Rowan', 'Sage', 'Emery', 'Harper', 'Reese', 'Skyler'];

// ------------------------------------------------------------------ nav -----
class Nav {
  constructor(d) {
    Object.assign(this, { x0: d.x0, y0: d.y0, c: d.cell, nx: d.nx, ny: d.ny, step: d.step });
    const zb = Uint8Array.from(atob(d.z), (ch) => ch.charCodeAt(0));
    const z16 = new Uint16Array(zb.buffer, 0, this.nx * this.ny);
    const N = this.nx * this.ny;
    this.z = new Float32Array(N);
    for (let i = 0; i < N; i++) this.z[i] = d.zbase + z16[i] / 100;
    const wb = Uint8Array.from(atob(d.walk), (ch) => ch.charCodeAt(0));
    this.walk = new Uint8Array(N);
    for (let i = 0; i < N; i++) this.walk[i] = (wb[i >> 3] >> (7 - (i & 7))) & 1;
    this.doors = d.doors;
    // keep only ground reachable from the front walk
    this.reach = new Uint8Array(N);
    const s = this.idx(d.start[0], d.start[1]);
    const q = [s]; this.reach[s] = 1;
    while (q.length) {
      const k = q.pop();
      this.neighbours(k, (n) => { if (!this.reach[n]) { this.reach[n] = 1; q.push(n); } });
    }
    this.g = new Float32Array(N); this.came = new Int32Array(N); this.seen = new Uint32Array(N); this.stamp = 0;
    this.heap = new Int32Array(N); this.f = new Float32Array(N);
  }
  idx(x, y) {
    const i = Math.floor((x - this.x0) / this.c), j = Math.floor((y - this.y0) / this.c);
    return i < 0 || j < 0 || i >= this.nx || j >= this.ny ? -1 : j * this.nx + i;
  }
  cx(k) { return this.x0 + ((k % this.nx) + 0.5) * this.c; }
  cy(k) { return this.y0 + (Math.floor(k / this.nx) + 0.5) * this.c; }
  ok(k) { return k >= 0 && this.walk[k] === 1 && (!this.reach || this.reach[k] === 1 || this.reach.length === 0); }
  zAt(x, y) { const k = this.idx(x, y); return k < 0 ? null : this.z[k]; }
  neighbours(k, fn) {
    const nx = this.nx, i = k % nx, j = (k - i) / nx, z = this.z[k];
    for (let dj = -1; dj <= 1; dj++) {
      for (let di = -1; di <= 1; di++) {
        if (!di && !dj) continue;
        const u = i + di, v = j + dj;
        if (u < 0 || v < 0 || u >= nx || v >= this.ny) continue;
        const n = v * nx + u;
        if (!this.walk[n] || Math.abs(this.z[n] - z) > this.step) continue;
        if (di && dj && (!this.walk[j * nx + u] || !this.walk[v * nx + i])) continue;
        fn(n, di && dj ? 1.4142 : 1);
      }
    }
  }
  snap(x, y, maxR = 8) {
    const k0 = this.idx(x, y);
    if (k0 >= 0 && this.walk[k0] && this.reach[k0]) return k0;
    let best = -1, bd = Infinity;
    const r = Math.ceil(maxR / this.c), i0 = Math.floor((x - this.x0) / this.c), j0 = Math.floor((y - this.y0) / this.c);
    for (let j = j0 - r; j <= j0 + r; j++) {
      for (let i = i0 - r; i <= i0 + r; i++) {
        if (i < 0 || j < 0 || i >= this.nx || j >= this.ny) continue;
        const k = j * this.nx + i;
        if (!this.walk[k] || !this.reach[k]) continue;
        const d = (this.cx(k) - x) ** 2 + (this.cy(k) - y) ** 2;
        if (d < bd) { bd = d; best = k; }
      }
    }
    return best;
  }
  // A* over the grid, then pulled tight with line-of-sight checks
  path(ax, ay, bx, by) {
    const s = this.snap(ax, ay), t = this.snap(bx, by);
    if (s < 0 || t < 0) return null;
    if (s === t) return [[bx, by]];
    const st = ++this.stamp, nx = this.nx, tx = t % nx, ty = (t - tx) / nx;
    const h = (k) => { const dx = Math.abs((k % nx) - tx), dy = Math.abs(Math.floor(k / nx) - ty); return Math.max(dx, dy) + 0.4142 * Math.min(dx, dy); };
    const heap = this.heap, f = this.f; let n = 0;
    const push = (k) => { let i = n++; heap[i] = k; while (i) { const p = (i - 1) >> 1; if (f[heap[p]] <= f[k]) break; heap[i] = heap[p]; i = p; } heap[i] = k; };
    const pop = () => { const top = heap[0]; const last = heap[--n]; let i = 0; for (;;) { let c = 2 * i + 1; if (c >= n) break; if (c + 1 < n && f[heap[c + 1]] < f[heap[c]]) c++; if (f[heap[c]] >= f[last]) break; heap[i] = heap[c]; i = c; } heap[i] = last; return top; };
    this.seen[s] = st; this.g[s] = 0; this.came[s] = -1; f[s] = h(s); push(s);
    const closed = new Set();
    let found = false, iter = 0;
    while (n && iter++ < 200000) {
      const k = pop();
      if (k === t) { found = true; break; }
      if (closed.has(k)) continue;
      closed.add(k);
      const gk = this.g[k];
      this.neighbours(k, (m, w) => {
        if (!this.reach[m]) return;
        const g2 = gk + w;
        if (this.seen[m] !== st || g2 < this.g[m]) {
          this.seen[m] = st; this.g[m] = g2; this.came[m] = k; f[m] = g2 + h(m); push(m);
        }
      });
    }
    if (!found) return null;
    const cells = [];
    for (let k = t; k !== -1; k = this.came[k]) cells.push(k);
    cells.reverse();
    const out = [];
    let i = 0;
    while (i < cells.length - 1) {
      let j = Math.min(cells.length - 1, i + 45);
      while (j > i + 1 && !this.los(cells[i], cells[j])) j--;
      out.push([this.cx(cells[j]), this.cy(cells[j])]);
      i = j;
    }
    out[out.length - 1] = this.ok(this.idx(bx, by)) ? [bx, by] : out[out.length - 1];
    return out;
  }
  los(a, b) {
    const ax = this.cx(a), ay = this.cy(a), bx = this.cx(b), by = this.cy(b);
    const d = Math.hypot(bx - ax, by - ay), n = Math.ceil(d / (this.c * 0.4));
    let prev = a;
    for (let s = 1; s <= n; s++) {
      const k = this.idx(ax + ((bx - ax) * s) / n, ay + ((by - ay) * s) / n);
      if (k < 0 || !this.walk[k] || !this.reach[k] || Math.abs(this.z[k] - this.z[prev]) > this.step) return false;
      prev = k;
    }
    return true;
  }
}

// ---------------------------------------------------------------- places ----
// Site coordinates (metres, x east, y north). `stand` = where a person stands or sits, with the
// direction they face (degrees, 0 = east, 90 = north). `inside` places hide the person at a door.
function placeDefs(nav) {
  const door = (id) => nav.doors.find((d) => d.id === id);
  const houseDoors = nav.doors.filter((d) => d.building === 'house' && d.kind === 'door');
  const at = (d, face) => ({ x: d.x, y: d.y, face: face ?? (Math.atan2(d.face[1], d.face[0]) * 180) / Math.PI, door: d });
  const fp = [4.931, 26.471];
  const ring = [45, 135, 225, 315].map((a) => {
    const r = (a * Math.PI) / 180;
    return { x: fp[0] + Math.cos(r) * 1.45, y: fp[1] + Math.sin(r) * 1.45, face: a + 180, sit: true, z: 259.52 };
  });
  return {
    house: { label: 'House', icon: '🏠', at: [0.6, 9.4], doors: houseDoors.map((d) => at(d)), inside: true, cap: 99 },
    porch: { label: 'Porch chairs', icon: '🪑', at: [6.0, 9.6], cap: 2, stand: [{ x: 5.55, y: 9.75, face: -90, sit: true, z: 262.15 }, { x: 6.55, y: 9.75, face: -90, sit: true, z: 262.15 }] },
    grill: { label: 'Grill on the deck', icon: '🍖', at: [-1.2, 26.95], cap: 1, stand: [{ x: -1.2, y: 26.25, face: 90 }] },
    hottub: { label: 'Hot tub', icon: '♨️', at: [14.72, 21.98], cap: 3, approach: { x: 14.72, y: 19.55 },
      stand: [{ x: 14.15, y: 21.55, face: 45, soak: true }, { x: 15.3, y: 21.55, face: 135, soak: true }, { x: 14.72, y: 22.55, face: -90, soak: true }] },
    firepit: { label: 'Fire pit', icon: '🔥', at: fp, cap: 4, stand: ring },
    veg: { label: 'Vegetable garden', icon: '🥕', at: [-36.7, 7.95], cap: 2, stand: [{ x: -33.65, y: 7.35, face: 180 }, { x: -33.65, y: 8.25, face: 180 }] },
    berries: { label: 'Berry arbors', icon: '🫐', at: [-38.4, 17.2], cap: 3, stand: [{ x: -37.35, y: 14.3, face: 180 }, { x: -37.15, y: 17.0, face: 180 }, { x: -36.95, y: 19.8, face: 180 }] },
    greenhouse: { label: 'Greenhouse', icon: '🌱', at: [-33.6, 18.1], cap: 1, doors: [at(door('site_gh_door'))], inside: true },
    barn: { label: 'Pole barn', icon: '🪚', at: [-41.4, -25.8], cap: 2, doors: [at(door('barn_overhead_door')), at(door('barn_man_door'))].filter((d) => d.door), inside: true },
    dgarage: { label: 'Detached garage', icon: '🔧', at: [-33.0, -6.0], cap: 1, doors: [at(door('dg_overhead_door')), at(door('dg_man_door'))].filter((d) => d.door), inside: true },
    car: { label: 'Car (attached garage)', icon: '🚗', at: [-6.8, 6.0], cap: 4, doors: [at(door('sides_garage_door'))], inside: true },
    lawn: { label: 'Back lawn', icon: '☁️', at: [-2.0, 42.0], cap: 99, stand: [{ x: -2.0, y: 43.0, face: 90 }] },
  };
}

// ---------------------------------------------------------------- actions ---
// gain: total change over the whole action. mins: duration in game minutes.
const ACTIONS = {
  meal: { place: 'house', label: 'Cook a meal', icon: '🍳', mins: 50, gain: { hunger: 70, fun: 5 }, skill: ['cooking', 0.3], cost: 10, trait: { foodie: { fun: 25 } }, pose: 'hidden' },
  snack: { place: 'house', label: 'Grab a snack', icon: '🥪', mins: 10, gain: { hunger: 22 }, cost: 3, pose: 'hidden' },
  salad: { place: 'house', label: 'Make a garden salad', icon: '🥗', mins: 20, gain: { hunger: 45, fun: 8 }, needs: 'produce', skill: ['cooking', 0.15], trait: { foodie: { fun: 15 }, greenthumb: { fun: 10 } }, pose: 'hidden' },
  shower: { place: 'house', label: 'Shower', icon: '🚿', mins: 20, gain: { hygiene: 85 }, pose: 'hidden' },
  toilet: { place: 'house', label: 'Use the bathroom', icon: '🚽', mins: 8, gain: { bladder: 100 }, pose: 'hidden' },
  sleep: { place: 'house', label: 'Sleep', icon: '🛏️', mins: 480, sleep: true, rate: { energy: 13 }, pose: 'hidden' },
  nap: { place: 'house', label: 'Take a nap', icon: '💤', mins: 60, gain: { energy: 18 }, pose: 'hidden' },
  tv: { place: 'house', label: 'Watch TV', icon: '📺', mins: 60, gain: { fun: 32, energy: -2 }, pose: 'hidden' },
  read: { place: 'house', label: 'Read a book', icon: '📖', mins: 45, gain: { fun: 20 }, pose: 'hidden' },
  phone: { place: 'house', label: 'Call a friend', icon: '☎️', mins: 30, gain: { social: 38, fun: 5 }, trait: { social: { social: 10 } }, pose: 'hidden' },
  porch: { place: 'porch', label: 'Sit on the porch', icon: '🪑', mins: 40, gain: { fun: 14, energy: 6 }, company: { social: 18 }, trait: { outdoorsy: { fun: 10 } }, pose: 'sit' },
  grill: { place: 'grill', label: 'Grill out on the deck', icon: '🍖', mins: 50, gain: { hunger: 80, fun: 15 }, cost: 15, skill: ['cooking', 0.35], trait: { foodie: { fun: 20 }, outdoorsy: { fun: 10 } }, pose: 'work' },
  soak: { place: 'hottub', label: 'Soak in the hot tub', icon: '♨️', mins: 45, gain: { fun: 32, hygiene: 15, energy: 8 }, company: { social: 22 }, night: { fun: 12 }, trait: { outdoorsy: { fun: 15 } }, pose: 'soak' },
  fire: { place: 'firepit', label: 'Sit by the fire', icon: '🔥', mins: 45, gain: { fun: 24 }, company: { social: 22 }, night: { fun: 15 }, trait: { outdoorsy: { fun: 10 } }, pose: 'sit' },
  smores: { place: 'firepit', label: 'Toast marshmallows', icon: '🍡', mins: 20, gain: { hunger: 12, fun: 18 }, cost: 2, company: { social: 10 }, pose: 'sit' },
  tend: { place: 'veg', label: 'Tend the vegetables', icon: '🥕', mins: 60, gain: { fun: 10, energy: -8, hygiene: -12 }, skill: ['gardening', 0.35], produce: 2, trait: { greenthumb: { fun: 25 } }, pose: 'tend' },
  berries: { place: 'berries', label: 'Pick berries', icon: '🫐', mins: 25, gain: { hunger: 12, fun: 12 }, produce: 1, months: [5, 9], trait: { greenthumb: { fun: 10 } }, pose: 'tend' },
  seedlings: { place: 'greenhouse', label: 'Start seedlings', icon: '🌱', mins: 45, gain: { fun: 16, hygiene: -6 }, skill: ['gardening', 0.3], trait: { greenthumb: { fun: 22 } }, pose: 'hidden' },
  woodwork: { place: 'barn', label: 'Build furniture to sell', icon: '🪚', mins: 120, gain: { fun: 8, energy: -16, hygiene: -16 }, earn: (p) => 45 + 25 * Math.floor(p.skills.handiness), skill: ['handiness', 0.4], trait: { handy: { fun: 28 } }, pose: 'hidden' },
  workout: { place: 'barn', label: 'Work out', icon: '💪', mins: 45, gain: { fun: 6, energy: -14, hygiene: -25 }, skill: ['fitness', 0.35], pose: 'hidden' },
  tinker: { place: 'dgarage', label: 'Tinker with the car', icon: '🔧', mins: 90, gain: { fun: 14, hygiene: -14 }, skill: ['handiness', 0.3], trait: { handy: { fun: 20 } }, pose: 'hidden' },
  work: { place: 'car', label: 'Drive to work', icon: '💼', mins: 540, work: true, pose: 'hidden' },
  clouds: { place: 'lawn', label: 'Watch the clouds', icon: '☁️', mins: 30, gain: { fun: 20, energy: 8 }, day: true, trait: { outdoorsy: { fun: 12 } }, pose: 'lie' },
  stars: { place: 'lawn', label: 'Stargaze', icon: '✨', mins: 40, gain: { fun: 28, energy: 4 }, night_only: true, trait: { nightowl: { fun: 15 }, outdoorsy: { fun: 10 } }, pose: 'lie' },
  jog: { place: 'lawn', label: 'Jog around the lawn', icon: '🏃', mins: 30, gain: { fun: 8, energy: -12, hygiene: -20 }, skill: ['fitness', 0.35], jog: true, pose: 'jog' },
  // social: done with another person
  chat: { social: true, label: 'Chat', icon: '💬', mins: 20, gain: { social: 28, fun: 6 }, rel: 5, pose: 'talk' },
  joke: { social: true, label: 'Tell a joke', icon: '😂', mins: 10, gain: { fun: 14, social: 12 }, rel: 4, risky: true, pose: 'talk' },
  hug: { social: true, label: 'Hug', icon: '🤗', mins: 5, gain: { social: 16, fun: 4 }, rel: 6, needRel: 30, pose: 'hug' },
  catch: { social: true, label: 'Play catch on the lawn', icon: '⚾', mins: 30, gain: { fun: 26, social: 18, energy: -6 }, rel: 5, skill: ['fitness', 0.1], day: true, place: 'lawn', pose: 'catch' },
};

// ---------------------------------------------------------------- people ----
const tmpV = new THREE.Vector3();
class Person {
  constructor(d) {
    Object.assign(this, {
      id: d.id, name: d.name, trait: d.trait, shirt: d.shirt, pants: d.pants || '#3a4655', skin: d.skin, hair: d.hair, kid: !!d.kid,
      needs: d.needs || { hunger: 80, energy: 85, hygiene: 85, bladder: 80, fun: 70, social: 70 },
      skills: d.skills || { cooking: 0, gardening: 0, handiness: 0, fitness: 0 },
      career: d.career || { level: 1, days: 0, lastDay: -1 },
      x: d.x ?? 2 + Math.random() * 2, y: d.y ?? 4, z: 0, face: d.face ?? 90,
    });
    this.queue = []; this.cur = null; this.idle = 0; this.hidden = false; this.anim = Math.random() * 10; this.bubble = '';
    this.build();
  }
  build() {
    const g = new THREE.Group(), s = this.kid ? 0.72 : 1;
    const mat = (c) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.75 });
    const skin = mat(this.skin), shirt = mat(this.shirt), pants = mat(this.pants), hair = mat(this.hair), shoe = mat('#20262c');
    const box = (w, h, d, m) => { const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); o.castShadow = true; return o; };
    const body = new THREE.Group(); body.scale.setScalar(s); g.add(body);
    this.legs = [];
    for (const sx of [-0.095, 0.095]) {
      const hip = new THREE.Group(); hip.position.set(sx, 0.88, 0);
      const leg = box(0.14, 0.82, 0.16, pants); leg.position.y = -0.41; hip.add(leg);
      const foot = box(0.14, 0.07, 0.26, shoe); foot.position.set(0, -0.85, 0.05); hip.add(foot);
      body.add(hip); this.legs.push(hip);
    }
    const upper = new THREE.Group(); upper.position.y = 0.88; body.add(upper); this.upper = upper;
    const torso = box(0.4, 0.6, 0.23, shirt); torso.position.y = 0.3; upper.add(torso);
    this.arms = [];
    for (const sx of [-0.255, 0.255]) {
      const sh = new THREE.Group(); sh.position.set(sx, 0.56, 0);
      const arm = box(0.1, 0.58, 0.11, shirt); arm.position.y = -0.26; sh.add(arm);
      const hand = box(0.09, 0.1, 0.1, skin); hand.position.y = -0.6; sh.add(hand);
      upper.add(sh); this.arms.push(sh);
    }
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.125, 16, 12), skin); head.position.y = 0.77; head.castShadow = true; upper.add(head);
    const cap = new THREE.Mesh(new THREE.SphereGeometry(0.133, 16, 10, 0, Math.PI * 2, 0, Math.PI * 0.55), hair); cap.position.set(0, 0.79, -0.012); upper.add(cap);
    const nose = box(0.03, 0.04, 0.04, skin); nose.position.set(0, 0.76, 0.125); upper.add(nose);
    this.head = head;
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.42, 0.52, 40), new THREE.MeshBasicMaterial({ color: 0xe4572e, transparent: true, opacity: 0.9, depthWrite: false, toneMapped: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.y = 0.03; ring.visible = false; g.add(ring); this.ring = ring;
    g.traverse((o) => { if (o.isMesh) o.userData.person = this; });
    this.body = body; this.group = g;
    V.scene.add(g);
    this.tag = el('button', 'life-tag');
    this.tag.addEventListener('click', (e) => { e.stopPropagation(); G.clickPerson(this, e); });
    $('life-overlay').append(this.tag);
  }
  get mood() {
    const n = this.needs;
    const w = { hunger: 1.2, energy: 1.1, hygiene: 0.8, bladder: 1.0, fun: 1.0, social: 0.9 };
    let s = 0, t = 0;
    for (const k in w) { s += w[k] * (n[k] < 15 ? n[k] * 0.6 : n[k]); t += w[k]; }
    return s / t;
  }
  get busy() { return !!this.cur; }
  worst() { let k = null, v = 101; for (const [n] of NEEDS) if (this.needs[n] < v) { v = this.needs[n]; k = n; } return [k, v]; }
  setPos(x, y) { this.x = x; this.y = y; }
  remove() { V.scene.remove(this.group); this.tag.remove(); }
  save() {
    return { id: this.id, name: this.name, trait: this.trait, shirt: this.shirt, pants: this.pants, skin: this.skin, hair: this.hair, kid: this.kid,
      needs: this.needs, skills: this.skills, career: this.career, x: this.cur?.inside ? this.cur.door.x : this.x, y: this.cur?.inside ? this.cur.door.y : this.y };
  }
}

// ------------------------------------------------------------------ game ----
class Game {
  async init() {
    const M = V.M;
    if (!M.nav) throw new Error('this build has no walkable-ground grid (nav.json)');
    const d = await (await fetch(V.asset(M.nav))).json();
    this.nav = new Nav(d);
    this.places = placeDefs(this.nav);
    for (const [id, p] of Object.entries(this.places)) {
      p.id = id;
      for (const s of p.stand || []) {
        if (s.soak) continue;
        const k = this.nav.snap(s.x, s.y, 3);
        if (k >= 0 && Math.hypot(this.nav.cx(k) - s.x, this.nav.cy(k) - s.y) > 0.5 && !s.z) { s.x = this.nav.cx(k); s.y = this.nav.cy(k); }
      }
      p.users = new Set();
    }
    this.people = [];
    this.speed = 1;
    this.follow = true;
    this.t = 7 * 60 + 30;          // game minutes since day 0 00:00
    this.money = 900;
    this.produce = 0;
    this.rel = {};
    this.lastHour = -1;
    this.billsDay = 0;
    this.buildUI();
    this.buildProps();
  }

  // ----------------------------------------------------------- lifecycle
  enter() {
    const v = V;
    this.active = true;
    document.body.classList.add('life-on');
    $('life').hidden = false;
    v.state.playing = false;
    if (v.state.measuring) v.toggleMeasure?.(false);
    v.select(null);
    this.props.visible = true;
    for (const p of this.people) p.group.visible = !p.hidden;
    v.state.game = this;
    this.viewerSun = { minutes: v.state.minutes, day: v.state.day };
    this.lastSunMin = null;
    if (!this.people.length) {
      const saved = this.loadSave();
      if (saved) this.askContinue(saved); else this.setupDialog();
    }
    v.goToView({ pos: [22, -22, v.M.z0 + 26], target: [-2, 12, v.M.z0] }, true);
    this.renderHUD();
  }
  exit() {
    this.active = false;
    this.save();
    document.body.classList.remove('life-on');
    $('life').hidden = true;
    this.closeMenu();
    V.state.game = null;
    this.props.visible = false;
    for (const p of this.people) p.group.visible = false;
    this.setGlow(false);
    if (this.viewerSun) { Object.assign(V.state, this.viewerSun); V.setSun(); }
    V.state.dirty = true;
  }

  // ------------------------------------------------------------- props
  buildProps() {
    const g = new THREE.Group(); g.name = 'life-props'; this.props = g; V.scene.add(g);
    const v3 = V.v3, m = (c, r = 0.8) => new THREE.MeshStandardMaterial({ color: c, roughness: r });
    const add = (geo, mat, x, y, z, rotDeg = 0) => { const o = new THREE.Mesh(geo, mat); o.position.copy(v3(x, y, z)); o.rotation.y = (rotDeg * Math.PI) / 180; o.castShadow = true; o.receiveShadow = true; g.add(o); return o; };
    const chair = (x, y, z, faceDeg, color) => {
      const c = new THREE.Group(), mat = m(color, 0.7);
      const part = (w, h, d, px, py, pz, rx = 0) => { const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); o.position.set(px, py, pz); o.rotation.x = rx; o.castShadow = true; c.add(o); };
      part(0.62, 0.05, 0.6, 0, 0.36, 0.05, -0.12); part(0.62, 0.7, 0.05, 0, 0.72, -0.3, -0.35);
      part(0.08, 0.05, 0.7, -0.33, 0.6, 0.02); part(0.08, 0.05, 0.7, 0.33, 0.6, 0.02);
      for (const sx of [-0.3, 0.3]) for (const sz of [-0.25, 0.3]) part(0.05, sz > 0 ? 0.6 : 0.4, 0.05, sx, sz > 0 ? 0.3 : 0.2, sz);
      c.position.copy(v3(x, y, z));
      const r = (faceDeg * Math.PI) / 180; c.rotation.y = Math.atan2(Math.cos(r), -Math.sin(r));
      g.add(c);
    };
    // porch: two teal chairs, as in the owner's photograph
    for (const s of this.places.porch.stand) chair(s.x, s.y + 0.08, s.z, s.face, '#2a9d9a');
    // fire pit chairs
    for (const s of this.places.firepit.stand) chair(s.x, s.y, s.z, s.face, '#8a6a4a');
    // grill on the rear deck
    const gz = this.nav.zAt(-1.2, 26.25) ?? 262.29;
    add(new THREE.BoxGeometry(0.9, 0.12, 0.5), m('#1d2125', 0.4), -1.2, 26.95, gz + 0.86);
    const lid = add(new THREE.CylinderGeometry(0.25, 0.25, 0.9, 16, 1, false, 0, Math.PI), m('#1d2125', 0.35), -1.2, 26.95, gz + 0.92);
    lid.rotation.z = Math.PI / 2; lid.rotation.y = 0;
    add(new THREE.BoxGeometry(0.95, 0.5, 0.45), m('#2c3238', 0.6), -1.2, 26.95, gz + 0.55);
    for (const sx of [-0.4, 0.4]) add(new THREE.BoxGeometry(0.05, 0.32, 0.05), m('#2c3238'), -1.2 + sx, 26.95, gz + 0.16);
    this.smoke = add(new THREE.SphereGeometry(0.18, 8, 6), new THREE.MeshBasicMaterial({ color: 0xdddddd, transparent: true, opacity: 0.0, depthWrite: false }), -1.2, 26.95, gz + 1.4);
    // hot tub water (shown with the cover off while someone soaks)
    const water = new THREE.Mesh(new THREE.BoxGeometry(2.1, 0.02, 2.14), new THREE.MeshStandardMaterial({ color: 0x3fa9c9, roughness: 0.15, transparent: true, opacity: 0.82 }));
    water.position.copy(v3(14.72, 21.98, 262.315)); water.rotation.y = (0.8 * Math.PI) / 180; water.visible = false; g.add(water); this.water = water;
    this.tubCover = [];
    for (const g2 of Object.values(V.groups)) g2?.traverse?.((o) => { if (o.isMesh && /hot_tub_(cover|lifter)/.test(o.name)) this.tubCover.push(o); });
    // fire in the pit
    const flame = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.5, 8), new THREE.MeshBasicMaterial({ color: 0xff8a2a, transparent: true, opacity: 0.9, toneMapped: false }));
    flame.position.copy(v3(4.931, 26.471, 259.52 + 0.62)); flame.visible = false; g.add(flame); this.flame = flame;
    this.fireLight = new THREE.PointLight(0xff9a40, 0, 9, 1.6); this.fireLight.position.copy(v3(4.931, 26.471, 260.4)); g.add(this.fireLight);
    // a ball for catch
    this.ball = add(new THREE.SphereGeometry(0.06, 10, 8), m('#f7f4ec', 0.5), 0, 0, 0); this.ball.visible = false;
    // the house's windows glow at night while someone is home
    this.houseGlass = [];
    V.groups.house?.traverse((o) => { if (o.isMesh && o.userData.glass) this.houseGlass.push(o); });
    g.visible = false;
  }
  // the viewer keeps nights bright enough to study; a game night is darker (applied after every setSun)
  nightLights() {
    if (!this.lights) this.lights = V.scene.children.filter((o) => o.isHemisphereLight || o.isDirectionalLight);
    const h = this.hour;
    this.dark = Math.max(clamp((h - 20.9) / 0.9, 0, 1), clamp((6.2 - h) / 0.9, 0, 1));
    for (const l of this.lights) l.intensity *= 1 - 0.72 * this.dark;
  }
  setGlow(on) {
    if (this.glowOn === on) return;
    this.glowOn = on;
    for (const o of this.houseGlass) {
      if (on) { o.userData.glowSave = o.userData.glowSave || o.material.emissive.getHex(); o.material.emissive.set(0x8a5a22); }
      else if (o.userData.glowSave != null) o.material.emissive.setHex(o.userData.glowSave);
    }
  }

  // ---------------------------------------------------------------- UI
  buildUI() {
    const root = el('div', 'life'); root.id = 'life'; root.hidden = true;
    root.innerHTML = `
      <div class="life-overlay" id="life-overlay"></div>
      <div class="life-top">
        <div class="life-clock"><b id="life-day"></b><span id="life-time"></span></div>
        <div class="life-speed" role="radiogroup" aria-label="Game speed">
          <button data-s="0" title="Pause (space)">❚❚</button><button data-s="1" title="Normal (1)">▶</button><button data-s="3" title="Fast (2)">▶▶</button><button data-s="12" title="Ultra (3)">▶▶▶</button>
        </div>
        <div class="life-money" id="life-money" title="Household funds"></div>
        <div class="life-produce" id="life-produce" title="Fresh produce from the garden"></div>
        <button class="life-follow" id="life-follow" aria-pressed="true" title="Camera follows the selected person (F)">Follow</button>
        <button class="life-exit" id="life-exit">Exit</button>
      </div>
      <div class="life-toasts" id="life-toasts" aria-live="polite"></div>
      <div class="life-dock">
        <div class="life-faces" id="life-faces"></div>
        <div class="life-card" id="life-card"></div>
      </div>
      <div class="life-menu" id="life-menu" hidden></div>
      <dialog class="life-dialog" id="life-dialog"></dialog>`;
    document.body.append(root);
    root.querySelectorAll('.life-speed button').forEach((b) => b.addEventListener('click', () => this.setSpeed(+b.dataset.s)));
    $('life-exit').addEventListener('click', () => this.exit());
    $('life-follow').addEventListener('click', () => { this.follow = !this.follow; $('life-follow').setAttribute('aria-pressed', String(this.follow)); });
    window.addEventListener('keydown', (e) => {
      if (!this.active || e.target.closest?.('input, select, textarea, dialog')) return;
      if (e.key === ' ') { e.preventDefault(); this.setSpeed(this.speed ? 0 : this.lastSpeed || 1); }
      else if (e.key === '1') this.setSpeed(1);
      else if (e.key === '2') this.setSpeed(3);
      else if (e.key === '3') this.setSpeed(12);
      else if (e.key === 'f' || e.key === 'F') $('life-follow').click();
      else if (e.key === 'Tab' && this.people.length) { e.preventDefault(); const i = this.people.indexOf(this.sel); this.selectPerson(this.people[(i + 1) % this.people.length]); }
      else if (e.key === 'Escape') this.closeMenu();
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden && this.active && this.speed) this.setSpeed(0); });
    // place markers
    this.pins = [];
    this.setSpeed(1);
  }
  makePins() {
    for (const p of Object.values(this.places)) {
      const b = el('button', 'life-pin', `<span>${p.icon}</span><em>${esc(p.label)}</em>`);
      b.addEventListener('click', (e) => { e.stopPropagation(); this.openPlaceMenu(p, e.clientX, e.clientY); });
      $('life-overlay').append(b);
      const gz = V.groundZ(p.at[0], p.at[1]);
      this.pins.push({ b, pos: V.v3(p.at[0], p.at[1], gz + (p.id === 'house' ? 4.2 : p.id === 'barn' ? 8.3 : p.id === 'dgarage' ? 5.4 : 2.4)) });
    }
  }
  setSpeed(s) {
    if (s) this.lastSpeed = s;
    this.speed = s;
    document.querySelectorAll('.life-speed button').forEach((b) => b.setAttribute('aria-checked', String(+b.dataset.s === s)));
    $('life')?.classList.toggle('paused', !s);
  }
  toast(msg, kind = '') {
    const t = el('div', `life-toast ${kind}`, msg);
    $('life-toasts').prepend(t);
    setTimeout(() => t.classList.add('out'), 4200);
    setTimeout(() => t.remove(), 4800);
    while ($('life-toasts').children.length > 4) $('life-toasts').lastChild.remove();
  }

  // ------------------------------------------------------ household setup
  loadSave() { try { const s = localStorage.getItem(SAVE_KEY); return s ? JSON.parse(s) : null; } catch { return null; } }
  save() {
    if (!this.people.length) return;
    const data = { v: 1, t: this.t, money: this.money, produce: this.produce, rel: this.rel, billsDay: this.billsDay, people: this.people.map((p) => p.save()) };
    try { localStorage.setItem(SAVE_KEY, JSON.stringify(data)); } catch { /* private window: no saving */ }
  }
  askContinue(s) {
    const d = $('life-dialog');
    d.innerHTML = `<h2>Welcome back</h2><p>${esc(s.people.map((p) => p.name).join(', '))} · day ${Math.floor(s.t / 1440) + 1} · ${money(s.money)}</p>
      <div class="life-row"><button class="primary" id="ld-cont">Continue</button><button id="ld-new">New household</button></div>`;
    d.showModal();
    $('ld-cont').onclick = () => { d.close(); this.load(s); };
    $('ld-new').onclick = () => { d.close(); this.setupDialog(); };
  }
  load(s) {
    Object.assign(this, { t: s.t, money: s.money, produce: s.produce || 0, rel: s.rel || {}, billsDay: s.billsDay || 0 });
    for (const pd of s.people) this.people.push(new Person(pd));
    this.afterMoveIn();
  }
  setupDialog() {
    const d = $('life-dialog');
    const rows = [];
    const used = new Set();
    let made = 0;
    const traits = Object.keys(TRAITS).sort(() => Math.random() - 0.5);
    const mk = (kid = false) => {
      let n; do { n = pick(NAMES); } while (used.has(n)); used.add(n);
      const i = made++;
      return { name: n, trait: traits[i % traits.length], shirt: SHIRTS[(i * 3) % SHIRTS.length], skin: pick(SKINS), hair: pick(HAIRS), kid };
    };
    rows.push(mk(), mk());
    const draw = () => {
      d.innerHTML = `<h2>Move in</h2><p class="lead">Who lives at 3910 W Dupont Rd? Up to four people.</p>
        <div class="life-setup">${rows.map((r, i) => `
          <div class="life-setup-row" data-i="${i}">
            <span class="life-face" style="--c:${r.shirt}">${esc(r.name[0] || '?')}</span>
            <input type="text" maxlength="14" value="${esc(r.name)}" aria-label="Name">
            <select aria-label="Trait">${Object.entries(TRAITS).map(([k, t]) => `<option value="${k}" ${k === r.trait ? 'selected' : ''}>${t.label}</option>`).join('')}</select>
            <label class="life-kid"><input type="checkbox" ${r.kid ? 'checked' : ''}> child</label>
            <span class="life-swatches">${SHIRTS.map((c) => `<button type="button" style="--c:${c}" aria-label="Shirt colour" aria-pressed="${c === r.shirt}"></button>`).join('')}</span>
            ${rows.length > 1 ? '<button type="button" class="life-x" aria-label="Remove">×</button>' : ''}
          </div>`).join('')}</div>
        <div class="life-row">${rows.length < 4 ? '<button type="button" id="ld-add">+ Add someone</button>' : ''}<button class="primary" id="ld-go">Move in</button></div>`;
      d.querySelectorAll('.life-setup-row').forEach((row) => {
        const r = rows[+row.dataset.i];
        row.querySelector('input[type=text]').oninput = (e) => { r.name = e.target.value.trim() || r.name; row.querySelector('.life-face').textContent = r.name[0]; };
        row.querySelector('select').onchange = (e) => { r.trait = e.target.value; };
        row.querySelector('.life-kid input').onchange = (e) => { r.kid = e.target.checked; };
        row.querySelectorAll('.life-swatches button').forEach((b, k) => { b.onclick = () => { r.shirt = SHIRTS[k]; draw(); }; });
        row.querySelector('.life-x')?.addEventListener('click', () => { rows.splice(+row.dataset.i, 1); draw(); });
      });
      $('ld-add')?.addEventListener('click', () => { rows.push(mk(true)); draw(); });
      $('ld-go').onclick = () => {
        d.close();
        rows.forEach((r, i) => this.people.push(new Person({ ...r, id: `p${i}`, x: 0.6 + i * 0.7, y: 6.6, face: 90 })));
        this.t = 7 * 60 + 30; this.money = 900; this.produce = 0; this.rel = {};
        this.afterMoveIn();
        this.toast(`Welcome home, ${esc(this.people.map((p) => p.name).join(' & '))}! Click a place or the ground to give orders.`);
      };
    };
    draw();
    d.showModal();
  }
  afterMoveIn() {
    if (!this.pins.length) this.makePins();
    for (const p of this.people) {
      const k = this.nav.snap(p.x, p.y);
      if (k >= 0) { p.x = this.nav.cx(k); p.y = this.nav.cy(k); }
      p.z = this.nav.z[k] ?? V.groundZ(p.x, p.y);
    }
    this.selectPerson(this.people[0]);
    this.renderHUD();
  }

  // --------------------------------------------------------- selection
  selectPerson(p) {
    this.sel = p;
    for (const q of this.people) q.ring.visible = q === p;
    this.closeMenu();
    this.renderHUD();
  }
  relKey(a, b) { return [a.id, b.id].sort().join('|'); }
  getRel(a, b) { return this.rel[this.relKey(a, b)] ?? 20; }
  addRel(a, b, d) { const k = this.relKey(a, b); this.rel[k] = clamp((this.rel[k] ?? 20) + d, -100, 100); }

  // ------------------------------------------------------------- input
  click(ray, e) {
    if (!this.active || !this.people.length) return true;
    if (!$('life-menu').hidden) { this.closeMenu(); return true; }
    const meshes = [];
    for (const p of this.people) if (!p.hidden) p.group.traverse((o) => { if (o.isMesh && o !== p.ring) meshes.push(o); });
    const hp = ray.intersectObjects(meshes, false)[0];
    if (hp) { this.clickPerson(hp.object.userData.person, e); return true; }
    const vis = V.modelMeshes.filter((o) => V.groups[o.userData.model]?.visible !== false);
    const hit = ray.intersectObjects(vis, false)[0];
    const ground = ray.intersectObject(V.terrain, false)[0];
    if (hit && (!ground || hit.distance <= ground.distance + 0.01)) {
      const place = this.placeForMesh(hit.object, hit.point);
      if (place) { this.openPlaceMenu(place, e.clientX, e.clientY); return true; }
    }
    const pt = hit || ground;
    if (pt && this.sel) {
      const x = pt.point.x, y = -pt.point.z;
      this.order(this.sel, { kind: 'goto', x, y, label: 'Walk here', icon: '👣' }, e.shiftKey);
    }
    return true;
  }
  placeForMesh(o, pt) {
    const n = o.name || '', m = o.userData.model;
    if (/hot_tub/.test(n)) return this.places.hottub;
    if (/fire_pit/.test(n)) return this.places.firepit;
    if (/porch/.test(n) && !/roof/.test(n)) return this.places.porch;
    if (/^garage_|garage_door|sides_garage/.test(n)) return this.places.car;
    if (m === 'house') {
      if (/rear_deck/.test(n)) return this.places.grill;
      if (/deck|railing|stairs|patio|walkout|river_rock|foundation/.test(n)) return null;
      return this.places.house;
    }
    if (m === 'pole_barn') return this.places.barn;
    if (m === 'garage_detached') return this.places.dgarage;
    if (m === 'greenhouse') return this.places.greenhouse;
    if (m === 'garden') {
      const x = pt.x, y = -pt.z;
      const near = ['veg', 'berries', 'greenhouse'].map((k) => this.places[k]).sort((a, b) => Math.hypot(a.at[0] - x, a.at[1] - y) - Math.hypot(b.at[0] - x, b.at[1] - y))[0];
      return Math.hypot(near.at[0] - x, near.at[1] - y) < 6 ? near : null;
    }
    return null;
  }
  clickPerson(p, e) {
    if (!this.sel || p === this.sel) { this.selectPerson(p); return; }
    const me = this.sel;
    const items = [{ label: `Control ${p.name}`, icon: '👤', run: () => this.selectPerson(p) }];
    for (const [id, a] of Object.entries(ACTIONS)) {
      if (!a.social) continue;
      const why = this.whyNotSocial(me, p, a);
      items.push({ label: a.label, icon: a.icon, hint: why || `${a.mins} min`, disabled: !!why, run: () => this.orderSocial(me, p, id) });
    }
    this.showMenu(`${esc(me.name)} → ${esc(p.name)}`, `Relationship ${Math.round(this.getRel(me, p))}`, items, e.clientX, e.clientY);
  }
  openPlaceMenu(place, cx, cy) {
    const p = this.sel;
    if (!p) return;
    const items = [];
    for (const [id, a] of Object.entries(ACTIONS)) {
      if (a.place !== place.id || a.social) continue;
      const why = this.whyNot(p, id);
      const bits = [a.sleep ? 'until rested' : a.work ? '9 to 5' : `${a.mins} min`];
      if (a.cost) bits.push(money(-a.cost));
      if (a.earn) bits.push(`earns ~${money(a.earn(p))}`);
      items.push({ label: a.label, icon: a.icon, hint: why || bits.join(' · '), disabled: !!why, run: () => this.order(p, { kind: 'act', id }) });
    }
    if (place.id === 'lawn') {
      for (const q of this.people) {
        if (q === p) continue;
        const why = this.whyNotSocial(p, q, ACTIONS.catch);
        items.push({ label: `Play catch with ${q.name}`, icon: '⚾', hint: why || '30 min', disabled: !!why, run: () => this.orderSocial(p, q, 'catch') });
      }
    }
    const users = [...place.users].map((u) => u.name);
    this.showMenu(`${place.icon} ${esc(place.label)}`, users.length ? `In use by ${esc(users.join(', '))}` : esc(p.name), items, cx, cy);
  }
  showMenu(title, sub, items, cx, cy) {
    const m = $('life-menu');
    m.innerHTML = `<header><b>${title}</b><small>${sub}</small></header>`;
    for (const it of items) {
      const b = el('button', '', `<span class="i">${it.icon}</span><span class="l">${esc(it.label)}</span><small>${esc(it.hint || '')}</small>`);
      b.disabled = !!it.disabled;
      b.onclick = (e) => { e.stopPropagation(); this.closeMenu(); it.run(); };
      m.append(b);
    }
    m.hidden = false;
    const r = m.getBoundingClientRect();
    m.style.left = `${clamp(cx + 8, 8, innerWidth - r.width - 8)}px`;
    m.style.top = `${clamp(cy - 20, 60, innerHeight - r.height - 8)}px`;
  }
  closeMenu() { const m = $('life-menu'); if (m) m.hidden = true; }

  // ----------------------------------------------------------- the clock
  get hour() { return (this.t / 60) % 24; }
  get dayIndex() { return Math.floor(this.t / 1440); }
  get weekday() { return this.dayIndex % 7; }       // day 0 is a Monday
  get isNight() { const h = this.hour; return h >= 20.5 || h < 6; }
  get season() { return 152 + (this.dayIndex % 120); }   // June 1 onward: leaves out, long evenings

  // ------------------------------------------------------- availability
  whyNot(p, id) {
    const a = ACTIONS[id];
    const h = this.hour;
    if (a.cost && this.money < a.cost) return 'Not enough money';
    if (a.needs === 'produce' && this.produce < 1) return 'No produce - tend the garden first';
    if (a.work) {
      if (p.kid) return 'Kids don\'t have jobs';
      if (this.weekday > 4) return 'No work on weekends';
      if (h < 6 || h > 10) return 'Leave between 6 and 10 am';
      if (p.career.lastDay === this.dayIndex) return 'Already worked today';
    }
    if (a.day && (h < 7 || h > 19.5)) return 'Daylight only';
    if (a.night_only && !this.isNight) return 'After dark';
    if (a.months) { const mo = new Date(2026, 0, this.season).getMonth(); if (mo < a.months[0] || mo > a.months[1]) return 'Out of season'; }
    if (a.sleep && p.needs.energy > 85) return 'Not tired';
    if (id === 'woodwork' && p.kid) return 'Too young for power tools';
    const pl = this.places[a.place];
    if (pl && pl.users.size >= pl.cap && !pl.users.has(p)) return 'Full right now';
    return '';
  }
  whyNotSocial(a, b, act) {
    if (b.cur?.inside || b.cur?.atWork) return `${b.name} is ${b.cur.atWork ? 'at work' : 'inside'}`;
    if (b.cur && b.cur.kind === 'social') return `${b.name} is busy`;
    if (act.needRel && this.getRel(a, b) < act.needRel) return 'Not close enough yet';
    if (act.day && (this.hour < 7 || this.hour > 19.5)) return 'Daylight only';
    return '';
  }

  // ------------------------------------------------------------ orders
  order(p, task, append = false) {
    if (!append) { this.cancel(p); p.queue.length = 0; }
    if (p.queue.length >= 4) p.queue.shift();
    p.queue.push(task);
    p.idle = 0;
    this.renderHUD();
  }
  orderSocial(a, b, id) {
    const act = ACTIONS[id];
    const s = { id, act, people: [a, b], arrived: new Set(), started: false, left: act.mins };
    // where they meet: the lawn spots for catch, else a point between them
    let spots;
    if (id === 'catch') spots = [[-5.5, 40.5], [3.5, 40.5]];
    else {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      let dx = b.x - a.x, dy = b.y - a.y; const L = Math.hypot(dx, dy) || 1; dx /= L; dy /= L;
      spots = [[mx - dx * 0.55, my - dy * 0.55], [mx + dx * 0.55, my + dy * 0.55]];
    }
    spots = spots.map(([x, y]) => { const k = this.nav.snap(x, y); return [this.nav.cx(k), this.nav.cy(k)]; });
    s.spots = spots;
    this.cancel(a); a.queue.length = 0;
    this.cancel(b); b.queue.length = 0;
    a.queue.push({ kind: 'social', s, me: 0 });
    b.queue.push({ kind: 'social', s, me: 1 });
    a.idle = b.idle = 0;
    this.renderHUD();
  }
  cancel(p) {
    const c = p.cur;
    if (!c) return;
    if (c.inside && c.phase === 'do') { this.finish(p, true); return; }
    if (c.kind === 'social') {
      for (const q of c.s.people) if (q !== p && q.cur?.s === c.s) { q.cur = null; q.pose = 'stand'; }
    }
    if (c.place) c.place.users.delete(p);
    if (c.phase === 'do' && c.act) this.applyPartial(p, c);
    p.cur = null; p.pose = 'stand'; p.path = null;
    if (c.soak) this.updateTub();
  }

  begin(p, task) {
    const c = { ...task, phase: 'walk', t: 0 };
    if (task.kind === 'goto') {
      c.dest = { x: task.x, y: task.y };
    } else if (task.kind === 'social') {
      const [x, y] = task.s.spots[task.me];
      c.dest = { x, y };
      c.act = task.s.act;
    } else {
      const a = ACTIONS[task.id];
      const why = this.whyNot(p, task.id);
      if (why) { if (p === this.sel) this.toast(`${esc(p.name)}: ${esc(a.label)} - ${esc(why)}`, 'warn'); return null; }
      c.act = a; c.place = this.places[a.place];
      c.place.users.add(p);
      if (c.place.inside) {
        c.door = c.place.doors.slice().sort((d1, d2) => Math.hypot(d1.x - p.x, d1.y - p.y) - Math.hypot(d2.x - p.x, d2.y - p.y))[0];
        c.dest = { x: c.door.x, y: c.door.y };
        c.inside = true;
      } else {
        const taken = new Set([...c.place.users].filter((u) => u !== p && u.cur?.seat).map((u) => u.cur.seat));
        c.seat = (c.place.stand || []).find((s) => !taken.has(s)) || c.place.stand[0];
        c.dest = c.place.approach || { x: c.seat.x, y: c.seat.y };
        if (c.seat.soak) c.soak = true;
      }
      if (a.work) c.atWork = true;
      if (a.jog) c.jog = true;
    }
    p.path = this.nav.path(p.x, p.y, c.dest.x, c.dest.y);
    if (!p.path) { if (p === this.sel) this.toast(`${esc(p.name)} can't get there`, 'warn'); c.place?.users.delete(p); return null; }
    return c;
  }

  // -------------------------------------------------------------- tick
  update(realDt) {
    if (!this.active) return;
    this.tickNo = (this.tickNo || 0) + 1;
    const gm = realDt * this.speed;            // game minutes passed (speed 1 = 1 game minute per second)
    if (gm > 0) {
      const before = this.t;
      this.t += gm;
      this.onTime(before, this.t);
      for (const p of this.people) this.decay(p, gm);
    }
    for (const p of this.people) this.step(p, gm, realDt);
    this.animate(realDt);
    this.syncWorld(realDt);
    this.layout();
    this.hudTimer = (this.hudTimer || 0) + realDt;
    if (this.hudTimer > 0.25) { this.hudTimer = 0; this.renderHUD(true); }
  }
  onTime(t0, t1) {
    const h0 = Math.floor(t0 / 60), h1 = Math.floor(t1 / 60);
    if (h1 !== h0) {
      if (h1 % 6 === 0) this.save();
      const day = Math.floor(t1 / 1440);
      if ((h1 % 24) >= 9 && this.billsDay < day + 1 && day > 0 && day % 3 === 0 && this.billsDay !== day) {
        this.billsDay = day;
        const bill = 90 + 25 * this.people.length;
        this.money -= bill;
        this.toast(`Bills paid: ${money(bill)}. Funds now ${money(this.money)}.`, this.money < 0 ? 'bad' : '');
      }
    }
  }
  decay(p, gm) {
    const h = gm / 60, n = p.needs, c = p.cur;
    const doing = c && c.phase === 'do' ? c : null;
    let r = { hunger: 5.5, energy: 4.2, hygiene: 3.4, bladder: 8, fun: 4.5, social: p.trait === 'social' ? 5 : 3.4 };
    if (p.kid) r.energy = 5.2;
    if (doing?.act?.sleep) r = { hunger: 2, energy: -(doing.act.rate.energy), hygiene: 1, bladder: 2.6, fun: 0, social: 0 };
    else if (doing?.atWork) r = { hunger: 3.5, energy: 5.5, hygiene: 2.5, bladder: 0, fun: 5.5, social: -2 };
    if (p.trait === 'nightowl' && this.isNight) r.energy *= 0.6;
    for (const k in r) n[k] = clamp(n[k] - r[k] * h, 0, 100);
    if (doing && doing.act && !doing.act.sleep && !doing.atWork) {
      const g = this.gains(p, doing);
      const f = gm / doing.act.mins;
      for (const k in g) n[k] = clamp(n[k] + g[k] * f, 0, 100);
      if (doing.act.skill) { const [s, v] = doing.act.skill; p.skills[s] = Math.min(5, p.skills[s] + (v / 3) * f * (1 - p.skills[s] / 7)); }
    }
    // a need running out interrupts whatever they chose to do (orders from the player hold out a little longer)
    if (doing && doing.kind !== 'collapse' && !doing.act?.sleep && !doing.atWork && doing.kind !== 'social') {
      const lim = doing.auto ? 12 : 5;
      const k = ['energy', 'bladder', 'hunger'].find((q) => n[q] < lim && !(doing.act?.gain?.[q] > 0));
      if (k) {
        this.finish(p, true);
        p.queue.length = 0; p.idle = 999;
        if (p === this.sel) this.toast(`${NEEDS.find((q) => q[0] === k)[2]} ${esc(p.name)} has to stop: ${k === 'energy' ? 'exhausted' : k === 'bladder' ? 'needs the bathroom' : 'starving'}.`, 'warn');
      }
    }
    // consequences
    if (n.bladder <= 0 && !(doing?.act?.sleep)) {
      n.bladder = 100; n.hygiene = 0; n.fun = Math.max(0, n.fun - 25);
      this.toast(`😳 ${esc(p.name)} didn't make it to the bathroom in time.`, 'bad');
    }
    if (n.energy <= 0 && !(doing?.act?.sleep) && !c?.inside) {
      this.cancel(p); p.queue.length = 0;
      p.cur = { kind: 'collapse', phase: 'do', t: 0, mins: 180, pose: 'lie', label: 'Passed out', icon: '😴' };
      this.toast(`😴 ${esc(p.name)} fell asleep right where they stood.`, 'bad');
    }
    if (p.cur?.kind === 'collapse') n.energy = clamp(n.energy + 14 * h, 0, 100);
    if (n.hunger < 8 && !p.warnedHunger) { p.warnedHunger = true; this.toast(`🍽️ ${esc(p.name)} is starving!`, 'bad'); }
    if (n.hunger > 30) p.warnedHunger = false;
  }
  gains(p, c) {
    const a = c.act, g = { ...(a.gain || {}) };
    const add = (o) => { for (const k in o || {}) g[k] = (g[k] || 0) + o[k]; };
    add(a.trait?.[p.trait]);
    if (a.night && this.isNight) add(a.night);
    if (p.trait === 'nightowl' && this.isNight && g.fun > 0) g.fun *= 1.5;
    if (a.company && c.place && [...c.place.users].some((u) => u !== p && u.cur?.phase === 'do')) add(a.company);
    if (a.social && p.trait === 'social' && g.social) g.social *= 1.3;
    return g;
  }
  applyPartial() { /* gains are applied continuously while doing; nothing extra on interruption */ }

  step(p, gm, realDt) {
    // pick the next job
    if (!p.cur) {
      if (p.queue.length) {
        const task = p.queue.shift();
        p.cur = this.begin(p, task);
        if (!p.cur) { p.pose = 'stand'; return; }
      } else if (gm > 0) {
        p.idle += gm;
        if (p.idle > (p === this.sel ? 25 : 8)) { p.idle = 0; this.autonomy(p); }
      }
    }
    const c = p.cur;
    if (!c) { p.pose = 'stand'; return; }
    if (c.phase === 'walk') {
      const speed = (c.jog ? 2.6 : p.kid ? 1.15 : 1.35) * Math.max(gm / Math.max(realDt, 1e-6), 0) / 1;
      let budget = gm > 0 ? speed * realDt : 0;
      p.pose = c.jog ? 'jog' : 'walk';
      while (budget > 0 && p.path && p.path.length) {
        const [tx, ty] = p.path[0];
        const dx = tx - p.x, dy = ty - p.y, d = Math.hypot(dx, dy);
        if (d > 1e-4) p.face = (Math.atan2(dy, dx) * 180) / Math.PI;
        if (d <= budget) { p.x = tx; p.y = ty; p.path.shift(); budget -= d; } else { p.x += (dx / d) * budget; p.y += (dy / d) * budget; budget = 0; }
      }
      if (!gm) p.pose = 'stand';
      if (p.path && p.path.length) return;
      this.arrive(p, c);
      return;
    }
    if (c.phase === 'wait') {
      p.pose = 'stand'; this.faceOther(p, c);
      c.wt = (c.wt || 0) + gm;
      const o = c.s.people[1 - c.me];
      if (c.wt > 90 || (o.cur?.s !== c.s && !o.queue.some((t) => t.s === c.s))) { c.s.dead = true; p.cur = null; p.pose = 'stand'; }
      return;
    }
    if (c.phase === 'do') {
      c.t += gm;
      if (c.kind === 'social') this.faceOther(p, c);
      const a = c.act;
      const done = c.kind === 'collapse' ? c.t >= c.mins
        : a?.sleep ? (p.needs.energy >= 99 && (this.hour >= 5.5 && this.hour < 12)) || p.needs.energy >= 100 && c.t > 60 || c.t > 720
        : a?.work ? (this.hour >= 17 && this.hour < 18) || c.t >= 660
        : c.kind === 'social' ? c.s.left <= 0 || c.s.dead || c.s.people[1 - c.me].cur?.s !== c.s
        : c.t >= a.mins;
      if (c.kind === 'social' && c.s.tick !== this.tickNo) { c.s.tick = this.tickNo; c.s.left -= gm; }
      if (done) this.finish(p);
    }
  }
  faceOther(p, c) {
    if (c.kind !== 'social') return;
    const o = c.s.people[1 - c.me];
    p.face = (Math.atan2(o.y - p.y, o.x - p.x) * 180) / Math.PI;
  }
  arrive(p, c) {
    if (c.kind === 'goto') { p.cur = null; p.pose = 'stand'; return; }
    if (c.kind === 'social') {
      if (c.s.dead) { p.cur = null; p.pose = 'stand'; return; }
      c.s.arrived.add(p);
      if (c.s.arrived.size < 2) { c.phase = 'wait'; return; }
      for (const q of c.s.people) {
        if (q.cur?.s !== c.s) continue;
        q.cur.phase = 'do'; q.cur.t = 0; q.pose = c.act.pose;
      }
      if (c.s.id === 'joke' && Math.random() < 0.25 + (this.getRel(c.s.people[0], c.s.people[1]) < 10 ? 0.3 : 0)) c.s.flop = true;
      return;
    }
    if (c.inside) {
      p.face = (Math.atan2(c.door.door.at[1] - p.y, c.door.door.at[0] - p.x) * 180) / Math.PI;
      p.hidden = true;
    } else if (c.seat) {
      p.x = c.seat.x; p.y = c.seat.y; p.face = c.seat.face;
    }
    c.phase = 'do'; c.t = 0;
    p.pose = c.act.pose;
    if (c.act.cost) this.money -= c.act.cost;
    if (c.act.needs === 'produce') this.produce = Math.max(0, this.produce - 1);
    if (c.act.work) { p.career.lastDay = this.dayIndex; this.toast(`🚗 ${esc(p.name)} drove off to work.`); }
    if (c.act.jog) { c.route = [[-14, 33], [14, 33], [14, 55], [-14, 55]]; c.leg = 0; c.loop = []; }
    if (c.soak) this.updateTub();
  }
  finish(p, interrupted = false) {
    const c = p.cur;
    p.cur = null; p.pose = 'stand';
    if (!c) return;
    const done = c.kind === 'social' ? c.s.id : c.id;
    if (done) (p.recent ||= {})[done] = this.t;
    if (c.place) c.place.users.delete(p);
    if (c.inside) { p.hidden = false; p.x = c.door.x; p.y = c.door.y; p.face = (Math.atan2(-c.door.door.face[1], -c.door.door.face[0]) * 180) / Math.PI; }
    if (c.soak) { this.updateTub(); const s = c.seat; const k = this.nav.snap(s.x, s.y - 1.6); p.x = this.nav.cx(k); p.y = this.nav.cy(k); }
    if (interrupted) return;
    const a = c.act;
    if (!a) return;
    if (a.earn) { const e = a.earn(p); this.money += e; this.toast(`🪚 ${esc(p.name)} sold a piece for ${money(e)}.`, 'good'); }
    if (a.produce) {
      const n = a.produce + Math.floor(p.skills.gardening / 2);
      this.produce += n;
      this.toast(`${a.icon} ${esc(p.name)} brought in ${n} basket${n > 1 ? 's' : ''} of produce.`, 'good');
    }
    if (a.work) {
      p.career.days += 1;
      const pay = 140 + 35 * (p.career.level - 1);
      this.money += pay;
      let msg = `💼 ${esc(p.name)} is home from work: ${money(pay)}.`;
      if (p.career.days % 5 === 0 && p.career.level < 8) { p.career.level += 1; msg += ` Promoted to level ${p.career.level}!`; }
      this.toast(msg, 'good');
    }
    if (c.kind === 'social' && c.s.people[0] === p) {
      const [x, y] = c.s.people;
      if (c.s.flop) { this.addRel(x, y, -6); x.needs.fun = Math.max(0, x.needs.fun - 10); this.toast(`😬 ${esc(y.name)} didn't find ${esc(x.name)}'s joke funny.`); }
      else this.addRel(x, y, a.rel || 3);
    }
    if (c.kind === 'social') {
      const g = this.gains(p, c);
      if (c.s.flop && c.s.people[0] === p) return;
      for (const k in g) p.needs[k] = clamp(p.needs[k] + g[k], 0, 100);
      if (a.skill) { const [s, v] = a.skill; p.skills[s] = Math.min(5, p.skills[s] + v / 3); }
    }
    const before = Math.floor((p.skills[a.skill?.[0]] || 0));
    if (a.skill && Math.floor(p.skills[a.skill[0]]) > (p.lastSkillLevel?.[a.skill[0]] ?? 0)) {
      p.lastSkillLevel = { ...(p.lastSkillLevel || {}), [a.skill[0]]: before };
      this.toast(`⭐ ${esc(p.name)} reached ${a.skill[0]} level ${before}!`, 'good');
    }
  }
  updateTub() {
    const soaking = this.people.some((p) => p.cur?.soak && p.cur.phase === 'do');
    this.water.visible = soaking;
    for (const o of this.tubCover) o.visible = !soaking;
  }

  // ---------------------------------------------------------- autonomy
  autonomy(p) {
    const n = p.needs, h = this.hour;
    // how much a need matters: rises steeply as it empties
    const want = (k) => { const v = n[k]; return ((100 - v) / 100) ** 2 * (v < 20 ? 3 : 1); };
    // an action is worth only what it can actually refill
    const worth = (g) => {
      let s = 0;
      for (const k in g) s += g[k] > 0 ? want(k) * Math.min(g[k], 100 - n[k] + 5) : want(k) * g[k] * 0.35;
      return s;
    };
    const recent = p.recent || {};
    const daylight = h >= 8 && h <= 19.5;
    let best = null, bs = 0.4;
    for (const [id, a] of Object.entries(ACTIONS)) {
      if (a.social || this.whyNot(p, id)) continue;
      const pl = this.places[a.place];
      let s = worth(this.gains(p, { act: a, place: pl }));
      if (a.sleep) s = (h >= 21.5 || h < 5 || n.energy < 22) ? want('energy') * 120 : 0;
      if (a.work) s = this.money < 250 ? 60 : 0;                 // work is the player's call, unless money runs low
      if (a.earn && this.money < 400) s += this.money < 200 ? 30 : 10;
      if (a.produce && this.produce < 3) s += 4;                 // the garden wants tending
      if (daylight && !pl.inside) s += 3 + (p.trait === 'outdoorsy' ? 4 : 0);   // a nice day outside
      if (id === 'nap' && h >= 18) s *= 0.2;                     // save it for bedtime
      const ago = this.t - (recent[id] ?? -1e9);                  // variety: the same thing again soon is less fun
      if (!a.sleep && id !== 'toilet' && ago < 360) s *= 0.25 + 0.75 * (ago / 360);
      s -= Math.hypot(pl.at[0] - p.x, pl.at[1] - p.y) * 0.05;
      s *= rand(0.8, 1.25);
      if (s > bs) { bs = s; best = id; }
    }
    // company: talk to someone who is out and free (or only wandering)
    if (n.social < 70 && !(h >= 22 || h < 6)) {
      const free = this.people.filter((q) => q !== p && !q.hidden && (!q.cur || q.cur.kind === 'goto' && q.cur.auto) && Math.hypot(q.x - p.x, q.y - p.y) < 40);
      const o = pick(free);
      if (o) {
        const id = this.getRel(p, o) >= 35 && Math.random() < 0.25 ? 'hug' : daylight && Math.random() < 0.3 ? 'catch' : Math.random() < 0.3 ? 'joke' : 'chat';
        const sc = worth(ACTIONS[id].gain) * (p.trait === 'social' ? 1.5 : 1) + 4;
        if (sc * rand(0.8, 1.25) > bs) { this.orderSocial(p, o, id); return; }
      }
    }
    if (best) p.queue.push({ kind: 'act', id: best, auto: true });
    else {
      const a = rand(0, 6.28), k = this.nav.snap(p.x + Math.cos(a) * 6, p.y + Math.sin(a) * 6);
      if (k >= 0) p.queue.push({ kind: 'goto', x: this.nav.cx(k), y: this.nav.cy(k), auto: true, label: 'Wander', icon: '👣' });
    }
  }

  // ---------------------------------------------------------- animation
  animate(dt) {
    const sp = Math.min(this.speed || 0, 3);
    for (const p of this.people) {
      p.anim += dt * (p.pose === 'walk' ? 7 * Math.max(sp, 1) : p.pose === 'jog' ? 11 * Math.max(sp, 1) : 3 * Math.max(sp, 1));
      const s = Math.sin(p.anim), c = p.cur;
      const [lL, lR] = p.legs, [aL, aR] = p.arms;
      let lift = 0, lean = 0, lx = 0, rx = 0, alx = 0, arx = 0, alz = 0, arz = 0, lie = false, sink = 0;
      switch (p.pose) {
        case 'walk': lx = s * 0.55; rx = -s * 0.55; alx = -s * 0.5; arx = s * 0.5; lift = Math.abs(Math.cos(p.anim)) * 0.03; break;
        case 'jog': lx = s * 0.9; rx = -s * 0.9; alx = -s * 0.9 - 0.4; arx = s * 0.9 - 0.4; lift = Math.abs(Math.cos(p.anim)) * 0.07; lean = 0.15; break;
        case 'sit': lx = rx = -1.45; lift = -0.43; alx = arx = -0.5; lean = -0.12; break;
        case 'soak': lx = rx = -1.45; lift = -0.43; alx = arx = -0.3; alz = 0.5; arz = -0.5; sink = 1; break;
        case 'tend': lean = 0.75 + s * 0.05; alx = -1.1 + s * 0.3; arx = -1.1 - s * 0.3; lx = rx = 0.25; lift = -0.08; break;
        case 'work': alx = -1.2 + s * 0.35; arx = -1.0 - s * 0.35; lean = 0.15; break;
        case 'talk': alx = -0.5 + Math.sin(p.anim * 1.7) * 0.4; arx = -0.3; alz = 0.2; break;
        case 'hug': alx = arx = -1.4; alz = 0.5; arz = -0.5; break;
        case 'catch': { const ph = (this.ballPhase || 0); arx = -2.6 + Math.sin(ph * Math.PI * 2) * 0.5; alx = -0.8; break; }
        case 'lie': lie = true; break;
        default: alx = Math.sin(p.anim * 0.5) * 0.03; arx = -alx;
      }
      lL.rotation.x = lx; lR.rotation.x = rx;
      aL.rotation.x = alx; aR.rotation.x = arx; aL.rotation.z = alz; aR.rotation.z = arz;
      p.upper.rotation.x = lean;
      p.body.rotation.x = lie ? -Math.PI / 2 : 0;
      p.body.position.y = lie ? 0.14 : lift * (p.kid ? 0.72 : 1);
      p.body.position.z = lie ? 0.85 * (p.kid ? 0.72 : 1) : 0;
      p.sink = sink;
    }
    // catch: a ball arcs between the two players
    const catchers = this.people.filter((p) => p.cur?.kind === 'social' && p.cur.s.id === 'catch' && p.cur.phase === 'do' && p.cur.me === 0);
    if (catchers.length) {
      const s = catchers[0].cur.s, [a, b] = s.people;
      this.ballPhase = ((this.ballPhase || 0) + dt * 0.7 * Math.max(Math.min(this.speed, 3), 0.0001)) % 2;
      const f = this.ballPhase % 1, from = this.ballPhase < 1 ? a : b, to = this.ballPhase < 1 ? b : a;
      const x = lerp(from.x, to.x, f), y = lerp(from.y, to.y, f), z = lerp(from.z, to.z, f) + 1.6 + Math.sin(f * Math.PI) * 2.2;
      this.ball.position.copy(V.v3(x, y, z)); this.ball.visible = true;
    } else this.ball.visible = false;
  }
  syncWorld(dt) {
    let anyHome = false, anyFire = false, grilling = false;
    for (const p of this.people) {
      const k = this.nav.idx(p.x, p.y);
      let tz = k >= 0 ? this.nav.z[k] : V.groundZ(p.x, p.y);
      const c = p.cur;
      if (c?.seat?.z && c.phase === 'do') tz = c.seat.z;
      if (c?.soak && c.phase === 'do') tz = 261.5;            // seated on the tub's bench, shoulders at the water
      if (c?.jog && c.phase === 'do') this.jogStep(p, dt);
      p.z = Math.abs(p.z - tz) > 2 ? tz : lerp(p.z, tz, Math.min(1, dt * 12));
      p.group.position.copy(V.v3(p.x, p.y, p.z));
      const r = (p.face * Math.PI) / 180;
      p.group.rotation.y = Math.atan2(Math.cos(r), -Math.sin(r));
      p.group.visible = this.active && !p.hidden;
      if (p.ring.visible) { p.ring.material.opacity = 0.6 + 0.3 * Math.sin(performance.now() / 300); }
      if (c?.inside && c.phase === 'do' && c.place.id === 'house') anyHome = true;
      if (c?.place?.id === 'firepit' && c.phase === 'do') anyFire = true;
      if (c?.place?.id === 'grill' && c.phase === 'do') grilling = true;
    }
    // sun and season follow the game clock
    const st = V.state;
    const mins = Math.round((this.t % 1440) * 2) / 2;
    if (mins !== this.lastSunMin || st.day !== this.season) {
      this.lastSunMin = mins;
      st.minutes = mins; st.day = this.season;
      V.setSun();
      this.nightLights();
    }
    const dark = this.hour >= 19.75 || this.hour < 6.5;
    this.setGlow(dark && anyHome);
    this.flame.visible = anyFire;
    if (anyFire) { const f = 0.85 + Math.sin(performance.now() / 90) * 0.1 + Math.sin(performance.now() / 37) * 0.05; this.flame.scale.set(f, f * 1.15, f); this.fireLight.intensity = dark ? 6 * f : 1.5; } else this.fireLight.intensity = 0;
    this.smoke.material.opacity = grilling ? 0.25 + 0.1 * Math.sin(performance.now() / 200) : 0;
    // camera follows the selected person
    if (this.follow && this.sel && !this.sel.hidden && this.speed) {
      const tgt = V.v3(this.sel.x, this.sel.y, this.sel.z + 1);
      const ctr = V.controls.target, cam = V.camera.position;
      const d = tgt.clone().sub(ctr).multiplyScalar(Math.min(1, dt * 2.5));
      if (d.length() > 0.002) { ctr.add(d); cam.add(d); }
    }
  }
  jogStep(p, dt) {
    const c = p.cur;
    if (!p.path || !p.path.length) {
      const [x, y] = c.route[c.leg % c.route.length]; c.leg += 1;
      p.path = this.nav.path(p.x, p.y, x, y) || [];
    }
    let budget = 2.6 * this.speed * dt;
    while (budget > 0 && p.path.length) {
      const [tx, ty] = p.path[0], dx = tx - p.x, dy = ty - p.y, d = Math.hypot(dx, dy);
      if (d > 1e-4) p.face = (Math.atan2(dy, dx) * 180) / Math.PI;
      if (d <= budget) { p.x = tx; p.y = ty; p.path.shift(); budget -= d; } else { p.x += (dx / d) * budget; p.y += (dy / d) * budget; budget = 0; }
    }
  }

  // ------------------------------------------------------- screen layout
  layout() {
    const cam = V.camera, w = innerWidth, h = innerHeight;
    const put = (elm, pos, dy = 0) => {
      tmpV.copy(pos).project(cam);
      if (tmpV.z > 1 || Math.abs(tmpV.x) > 1.05 || Math.abs(tmpV.y) > 1.05) { elm.style.display = 'none'; return; }
      elm.style.display = '';
      elm.style.transform = `translate(${Math.round((tmpV.x * 0.5 + 0.5) * w)}px, ${Math.round((-tmpV.y * 0.5 + 0.5) * h + dy)}px) translate(-50%, -100%)`;
    };
    const camPos = cam.position;
    for (const pin of this.pins) {
      put(pin.b, pin.pos);
      pin.b.classList.toggle('far', pin.pos.distanceTo(camPos) > 70);
    }
    for (const p of this.people) {
      if (!this.active) { p.tag.style.display = 'none'; continue; }
      const c = p.cur;
      const [wk, wv] = p.worst();
      let icon = '';
      if (c?.phase === 'do') icon = c.kind === 'social' ? (c.s.flop ? '😬' : c.act.icon) : (c.icon || c.act?.icon || '');
      else if (c?.phase === 'wait') icon = '⏳';
      if (wv < 22 && (!c || c.phase !== 'do')) icon = NEEDS.find((n) => n[0] === wk)[2];
      const where = c?.atWork && c.phase === 'do' ? 'at work' : c?.inside && c.phase === 'do' ? 'inside' : '';
      const html = `<b style="--c:${p.shirt}">${esc(p.name)}</b>${icon ? `<i>${icon}</i>` : ''}${where ? `<small>${where}</small>` : ''}`;
      if (p.tagHtml !== html) { p.tag.innerHTML = html; p.tagHtml = html; }
      p.tag.classList.toggle('sel', p === this.sel);
      const pos = p.hidden ? (c?.door ? V.v3(c.door.x, c.door.y, (this.nav.zAt(c.door.x, c.door.y) ?? p.z) + 2.4) : V.v3(p.x, p.y, p.z + 2.2))
        : V.v3(p.x, p.y, p.z + (p.pose === 'lie' ? 0.9 : (p.kid ? 1.55 : 2.05)) - (p.sink ? 0.4 : 0));
      put(p.tag, pos);
    }
  }

  // --------------------------------------------------------------- HUD
  renderHUD(light = false) {
    if (!$('life')) return;
    const d = this.dayIndex, h = this.hour;
    const hh = Math.floor(h), mm = Math.floor((h - hh) * 60);
    $('life-day').textContent = `Day ${d + 1} · ${DAYS[d % 7]}`;
    $('life-time').textContent = `${((hh + 11) % 12) + 1}:${String(mm).padStart(2, '0')} ${hh < 12 ? 'am' : 'pm'}`;
    $('life-money').textContent = money(this.money);
    $('life-money').classList.toggle('neg', this.money < 0);
    $('life-produce').textContent = `🧺 ${this.produce}`;
    // faces
    const faces = $('life-faces');
    if (!light || faces.children.length !== this.people.length) {
      faces.innerHTML = '';
      for (const p of this.people) {
        const b = el('button', 'life-face', esc(p.name[0]));
        b.style.setProperty('--c', p.shirt);
        b.title = p.name;
        b.onclick = () => { if (this.sel === p) this.recenter(p); else this.selectPerson(p); };
        faces.append(b);
      }
    }
    [...faces.children].forEach((b, i) => {
      const p = this.people[i]; if (!p) return;
      b.setAttribute('aria-pressed', String(p === this.sel));
      b.style.setProperty('--m', `${Math.round(p.mood)}%`);
      b.dataset.mood = p.mood > 66 ? 'good' : p.mood > 38 ? 'ok' : 'bad';
    });
    const p = this.sel, card = $('life-card');
    if (!p) { card.innerHTML = ''; return; }
    const c = p.cur;
    const now = c ? (c.kind === 'goto' ? (c.label || 'Walking') : c.kind === 'collapse' ? c.label : c.kind === 'social' ? `${c.act.label} with ${c.s.people[1 - c.me].name}` : c.act.label) : 'Idle';
    const icon = c ? (c.icon || c.act?.icon || '👣') : '·';
    const phase = c ? (c.phase === 'walk' ? ' (on the way)' : c.phase === 'wait' ? ' (waiting)' : '') : '';
    const q = p.queue.map((t, i) => {
      const a = t.kind === 'act' ? ACTIONS[t.id] : t.kind === 'social' ? t.s.act : { label: t.label || 'Walk', icon: t.icon || '👣' };
      return `<button class="life-q" data-i="${i}" title="Cancel">${a.icon} ${esc(a.label)} <span>×</span></button>`;
    }).join('');
    const moodWord = p.mood > 80 ? 'Very happy' : p.mood > 62 ? 'Happy' : p.mood > 42 ? 'Fine' : p.mood > 25 ? 'Uncomfortable' : 'Miserable';
    card.innerHTML = `
      <header><span class="life-face big" style="--c:${p.shirt}">${esc(p.name[0])}</span>
        <div><b>${esc(p.name)}</b><small>${esc(TRAITS[p.trait]?.label || '')}${p.kid ? ' · child' : ` · career level ${p.career.level}`} · ${moodWord}</small></div></header>
      <div class="life-now">${icon} <b>${esc(now)}</b>${phase}${c ? '<button class="life-q cur" data-i="-1" title="Cancel">×</button>' : ''}</div>
      ${q ? `<div class="life-queue">${q}</div>` : ''}
      <div class="life-needs">${NEEDS.map(([k, label, ic]) => {
        const v = Math.round(p.needs[k]);
        return `<div class="life-need" data-l="${v > 55 ? 'hi' : v > 25 ? 'mid' : 'lo'}"><span>${ic} ${label}</span><i style="--v:${v}%"></i></div>`;
      }).join('')}</div>
      <div class="life-skills">${SKILLS.map(([k, label]) => `<span title="${label} ${p.skills[k].toFixed(1)} / 5">${label} <b>${'●'.repeat(Math.floor(p.skills[k]))}${'○'.repeat(5 - Math.floor(p.skills[k]))}</b></span>`).join('')}</div>`;
    card.querySelectorAll('.life-q').forEach((b) => {
      b.onclick = () => {
        const i = +b.dataset.i;
        if (i < 0) this.cancel(p); else p.queue.splice(i, 1);
        p.idle = -30;
        this.renderHUD();
      };
    });
  }
  recenter(p) {
    const tgt = V.v3(p.x, p.y, p.z + 1), ctr = V.controls.target;
    V.camera.position.add(tgt.clone().sub(ctr)); ctr.copy(tgt);
  }
}
