// C03 VRM pet renderer: three.js + @pixiv/three-vrm inside the PySide6 pet window.
//
// Bridge contract (POST /__c03 -> Python, runJavaScript -> page):
//   out:  ready, failed, fps, clicked, drag_begin, context_menu, log
//   in :  window.__c03.configure({...}), setState, setCursor, react, resize, reload, reportFps
//
// Transparency contract: the renderer clears to (0, 0, 0, 0) with premultiplied alpha, so
// "nothing drawn" pixels are pure black. That is exactly the colour the Win32 colour key
// (LWA_COLORKEY, crKey = black) punches out, which is what gives per-pixel click-through.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const BRIDGE = '/__c03';
const canvas = document.getElementById('canvas');
const bubble = document.getElementById('bubble');
const banner = document.getElementById('banner');

const params = new URLSearchParams(location.search);
const MODEL_URL = params.get('model') || 'model/VRM1_Constraint_Twist_Sample.vrm';

const options = {
  lookAtMouse: true,
  idleAnimation: true,
  clickReaction: true,
  bubble: true,
  clickThrough: false,
};

// ---------------------------------------------------------------- click-through floor
//
// The pet window is composited by Qt with per-pixel alpha (UpdateLayeredWindow).
// Pixels at alpha=0 receive no mouse input, so click-through is native. Interactive
// mode paints an invisible floor (alpha 1/255) over the page so the whole window
// rectangle stays hittable. See apply_passthrough() in app/pet3d/window.py.
function applyBackgroundFloor() {
  document.body.style.background = options.clickThrough ? 'rgba(0, 0, 0, 0)' : 'rgba(0, 0, 0, 0.004)';
}
applyBackgroundFloor();

// ---------------------------------------------------------------- bridge

function post(method, args = {}) {
  const body = JSON.stringify({ method, args });
  try {
    if (navigator.sendBeacon) {
      navigator.sendBeacon(BRIDGE, new Blob([body], { type: 'application/json' }));
      return;
    }
  } catch (_) {
    /* fall through to fetch */
  }
  fetch(BRIDGE, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
    .catch(() => {});
}

function showBanner(text) {
  banner.textContent = text;
  banner.classList.add('visible');
}

// ---------------------------------------------------------------- three.js

const renderer = new THREE.WebGLRenderer({
  canvas,
  alpha: true,
  premultipliedAlpha: true,
  antialias: true,
  powerPreference: 'low-power',
});
renderer.setClearColor(0x000000, 0);
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(25, 1, 0.01, 50);

scene.add(new THREE.HemisphereLight(0xffffff, 0x556677, 1.8));
const key = new THREE.DirectionalLight(0xffffff, 2.0);
key.position.set(0.6, 1.0, 1.0);
scene.add(key);
const rim = new THREE.DirectionalLight(0x88ccff, 1.0);
rim.position.set(-0.8, 0.4, -0.6);
scene.add(rim);

let vrm = null;
let mixer = null;
let readySent = false;
const lookTarget = new THREE.Vector3(0, 0, 0);
let lookBlend = 0;
let reactionUntil = 0;
let reactionStart = 0;

// ---------------------------------------------------------------- model loading

function frameModel(target) {
  // Fit the character to the window with a margin, in world units derived from the model.
  const box = new THREE.Box3().setFromObject(target);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const height = Math.max(size.y, 0.01);
  const fov = (camera.fov * Math.PI) / 180;
  const distance = (height * 0.62) / Math.tan(fov / 2);
  const radius = Math.max(size.x, size.z, height * 0.4);
  camera.aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight);
  camera.position.set(center.x, center.y + height * 0.02, center.z + distance + radius);
  camera.lookAt(center.x, center.y + height * 0.02, center.z);
  camera.near = Math.max(0.01, distance - radius * 2);
  camera.far = distance + radius * 6;
  camera.updateProjectionMatrix();
}

async function loadModel() {
  readySent = false;
  banner.classList.remove('visible');
  try {
    // three-vrm 3.x: VRM loading is a GLTFLoader plugin, not a loader class. The plugin
    // assembles MToon materials, SpringBone and Constraints and leaves a VRM instance in
    // gltf.userData.vrm once afterRoot has run.
    const loader = new GLTFLoader();
    let plugin = null;
    loader.register((parser) => {
      plugin = new VRMLoaderPlugin(parser);
      return plugin;
    });
    const gltf = await loader.loadAsync(MODEL_URL);
    const loaded = gltf.userData.vrm;
    if (!loaded) {
      throw new Error('文件不含 VRMC_vrm 元数据，不是可用的 VRM 模型');
    }
    vrm = loaded;
    // VRM 0.x models face +Z after loading; VRM 1.0 models face -Z by convention.
    if (vrm.meta && vrm.meta.metaVersion !== undefined && String(vrm.meta.metaVersion).startsWith('0.')) {
      VRMUtils.rotateVRM0(vrm.scene);
      post('log', { message: 'VRM 0.x model rotated for the v3 pipeline' });
    }
    if (scene.getObjectByName('pet')) scene.remove(scene.getObjectByName('pet'));
    scene.add(vrm.scene);
    vrm.scene.name = 'pet';
    if (vrm.firstPerson) vrm.firstPerson.enabled = false;
    frameModel(vrm.scene);
    if (vrm.lookAt) vrm.lookAt.firstPerson = false;
    post('log', {
      message:
        'loaded ' +
        MODEL_URL +
        ' | ' +
        (vrm.meta ? [vrm.meta.name, vrm.meta.metaVersion, vrm.meta.copyrightInfo].filter(Boolean).join(' / ') : 'no meta'),
    });
    if (!readySent) {
      readySent = true;
      post('ready', { renderer: 'vrm', fps: fpsValue });
    }
  } catch (error) {
    const reason = (error && error.message) || String(error);
    showBanner('VRM 加载失败：' + reason);
    post('failed', { message: 'VRM 加载失败：' + reason });
  }
}

// ---------------------------------------------------------------- input

let downX = 0;
let downY = 0;
let moved = 0;
let dragging = false;

canvas.addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  downX = event.clientX;
  downY = event.clientY;
  moved = 0;
  dragging = false;
});

window.addEventListener('mousemove', (event) => {
  if (event.buttons & 1) {
    moved += Math.abs(event.movementX || 0) + Math.abs(event.movementY || 0);
    if (moved > 6 && !dragging) {
      dragging = true;
      post('drag_begin', { x: event.screenX, y: event.screenY });
    }
  }
});

window.addEventListener('mouseup', (event) => {
  if (event.button !== 0) return;
  if (dragging) post('drag_end', {});
  dragging = false;
  if (moved <= 6) post('clicked', { x: event.clientX, y: event.clientY });
});

canvas.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  post('context_menu', { x: event.screenX, y: event.screenY });
});

// ---------------------------------------------------------------- fps

let fpsValue = 0;
let frames = 0;
let lastFpsAt = performance.now();

function reportFps() {
  post('fps', { value: fpsValue });
}

// ---------------------------------------------------------------- animation

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = clock.getDelta();
  const t = clock.elapsedTime;

  if (vrm) {
    const humanoid = vrm.humanoid;
    const hips = humanoid && humanoid.getNormalizedBoneNode
      ? humanoid.getNormalizedBoneNode('hips')
      : null;
    const upperChest = humanoid && humanoid.getNormalizedBoneNode
      ? humanoid.getNormalizedBoneNode('upperChest')
      : null;

    if (hips) {
      const baseY = hips.position.y;
      if (options.idleAnimation) {
        // Slow vertical breathing plus a small sway: the classic idle loop.
        const breath = Math.sin(t * 1.35) * 0.004;
        const sway = Math.sin(t * 0.55) * 0.012;
        hips.position.y = baseY + breath;
        hips.rotation.z = sway * 0.35;
        hips.rotation.x = sway * 0.15;
        if (upperChest) {
          upperChest.rotation.x = Math.sin(t * 1.1 + 0.6) * 0.018;
        }
      }
    }

    if (vrm.lookAt && options.lookAtMouse) {
      lookBlend += (1 - lookBlend) * Math.min(1, dt * 4);
      vrm.lookAt.lookAt(lookTarget.x * lookBlend, lookTarget.y * lookBlend, lookTarget.z);
    }

    if (vrm.expressionManager) {
      const reacting = t < reactionUntil;
      const progress = reacting ? (t - reactionStart) / Math.max(0.001, reactionUntil - reactionStart) : 1;
      const happy = reacting ? Math.sin(progress * Math.PI) : 0;
      // Blink: a short spike once every ~3.5 s, independent of the click reaction.
      const blinkPhase = (t % 3.5) / 0.16;
      const blink = blinkPhase < 1 ? Math.sin(blinkPhase * Math.PI) : 0;
      try {
        vrm.expressionManager.setValue('happy', happy);
        vrm.expressionManager.setValue('relaxed', reacting ? happy * 0.4 : 0);
        vrm.expressionManager.setValue('blink', blink);
      } catch (_) {
        /* models without these expressions simply do not react */
      }
      if (reacting && vrm.scene) {
        const lift = Math.sin(progress * Math.PI) * 0.02;
        vrm.scene.position.y = lift;
      } else if (vrm.scene) {
        vrm.scene.position.y *= 0.85;
      }
    }

    if (vrm.update) vrm.update(dt);
  }

  if (mixer) mixer.update(dt);
  renderer.render(scene, camera);

  frames += 1;
  const now = performance.now();
  if (now - lastFpsAt >= 1000) {
    fpsValue = (frames * 1000) / (now - lastFpsAt);
    frames = 0;
    lastFpsAt = now;
  }
}

// ---------------------------------------------------------------- page API

function resize(width, height, dpr) {
  const w = width || canvas.clientWidth || 1;
  const h = height || canvas.clientHeight || 1;
  renderer.setPixelRatio(Math.min(dpr || window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (vrm) frameModel(vrm.scene);
}

window.addEventListener('resize', () => resize());
resize();
loadModel();
animate();
reportFps();
setInterval(reportFps, 1000);

window.__c03 = {
  configure(patch) {
    Object.assign(options, patch || {});
    applyBackgroundFloor();
    if (options.bubble === false) bubble.classList.remove('visible');
    if (vrm) frameModel(vrm.scene);
  },
  setState(state, caption) {
    if (!options.bubble || !caption) {
      bubble.classList.remove('visible');
      return;
    }
    bubble.textContent = caption;
    bubble.classList.add('visible');
  },
  setCursor(nx, ny) {
    // -1..1 screen space -> VRM look-at space (x right, y up, z forward).
    lookTarget.set(nx * 0.6, ny * 0.35, 1);
  },
  react() {
    if (!vrm || !options.clickReaction) return;
    reactionStart = clock.elapsedTime;
    reactionUntil = reactionStart + 0.8;
  },
  resize,
  reload() {
    loadModel();
  },
  reportFps,
};

post('log', { message: 'bridge api installed' });