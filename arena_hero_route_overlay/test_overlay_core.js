"use strict";

const assert = require("node:assert/strict");
const overlay = require("./overlay-core.js");

const camera = { x: 10.5, y: -4.25, cell: 32 };
const screen = overlay.gridToScreen([13, -2], camera, 1000, 600);
assert.deepEqual(screen, { x: 580, y: 372 });
assert.deepEqual(
  overlay.screenToGrid(screen.x, screen.y, camera, 1000, 600),
  [13, -2],
);
assert.equal(overlay.gridDistance([13, -2], [10, 4]), 9);
assert.equal(overlay.gridDistance([13, -2], null), null);
assert.equal(overlay.estimateTravelMinutes(12, 5), 1);
assert.equal(overlay.estimateTravelMinutes(0, 5), 0);

assert.deepEqual(
  overlay.pathTurnPoints([
    [0, 0],
    [0, -1],
    [0, -2],
    [1, -2],
    [2, -2],
    [2, -1],
  ]),
  [
    [0, 0],
    [0, -2],
    [2, -2],
    [2, -1],
  ],
);

const canvas = { parentElement: null };
Object.defineProperty(canvas, "__reactFiber$overlayTest", {
  value: {
    memoizedState: {
      memoizedState: { x: -34.5, y: 85.25, cell: 28 },
      baseState: null,
      queue: null,
      next: null,
    },
    memoizedProps: null,
    pendingProps: null,
    stateNode: null,
    return: null,
    alternate: null,
  },
});
assert.deepEqual(overlay.findCameraState(canvas), {
  x: -34.5,
  y: 85.25,
  cell: 28,
});

let centeredCamera = null;
const focusCanvas = { parentElement: null };
Object.defineProperty(focusCanvas, "__reactFiber$focusTest", {
  value: {
    memoizedState: {
      memoizedState: { width: 800, height: 600 },
      baseState: null,
      queue: { dispatch: () => {} },
      next: {
        memoizedState: { x: 8, y: -3, cell: 44 },
        baseState: null,
        queue: {
          dispatch: (update) => {
            centeredCamera = update({ x: 8, y: -3, cell: 44 });
          },
        },
        next: null,
      },
    },
    memoizedProps: null,
    pendingProps: null,
    stateNode: null,
    return: null,
    alternate: null,
  },
});
assert.equal(overlay.centerCameraOn(focusCanvas, [-52, -210]), true);
assert.deepEqual(centeredCamera, { x: -52, y: -210, cell: 44 });
assert.equal(overlay.centerCameraOn({}, [-52, -210]), false);

const resourceCanvas = { parentElement: null };
Object.defineProperty(resourceCanvas, "__reactFiber$resourceTest", {
  value: {
    memoizedState: {
      memoizedState: {
        resources: [
          [-62, 68],
          { position: [-61, 76], type: "iron_ore" },
          { x: -23, y: 86, kind: "resource_node" },
        ],
      },
      baseState: null,
      queue: null,
      next: null,
    },
    memoizedProps: null,
    pendingProps: null,
    stateNode: null,
    return: null,
    alternate: null,
  },
});
assert.deepEqual(overlay.findResourceCells(resourceCanvas), [
  [-62, 68],
  [-61, 76],
  [-23, 86],
]);

assert.deepEqual(
  overlay.normalizeSettings({
    lineWidth: 99,
    opacity: 0,
    workerColor: "#ABCDEF",
    rangerColor: "invalid",
    showRoutes: false,
  }),
  {
    ...overlay.DEFAULT_SETTINGS,
    showRoutes: false,
    lineWidth: 5,
    opacity: 0.1,
    workerColor: "#abcdef",
  },
);

assert.deepEqual(
  overlay.normalizeLogs({
    latest_tick: 9,
    entries: [
      {
        tick: 9,
        event_id: "event-9",
        category: "战斗",
        level: "danger",
        title: "单位阵亡",
        message: "先锋#4 阵亡",
        position: [3, -2],
      },
      { tick: "bad", event_id: "ignored", title: "x", message: "y" },
    ],
  }),
  {
    version: 1,
    latest_tick: 9,
    entries: [
      {
        version: 1,
        recorded_at: null,
        tick: 9,
        event_id: "event-9",
        source: "server",
        category: "战斗",
        level: "danger",
        title: "单位阵亡",
        message: "先锋#4 阵亡",
        event_type: null,
        reason_code: null,
        position: [3, -2],
        actor: null,
        target: null,
      },
    ],
  },
);

const desktopLayout = overlay.calculateControlLayout(
  { left: 100, top: 50, width: 1200, height: 700 },
  62,
  900,
);
assert.deepEqual(desktopLayout.dock, {
  left: 110,
  top: 60,
  width: 760,
  height: 62,
});
assert.equal(desktopLayout.stats.top, 130);
assert.equal(desktopLayout.stats.left, 110);
assert.equal(desktopLayout.stats.width, 390);
assert.equal(desktopLayout.stats.maxHeight, 610);

const narrowLayout = overlay.calculateControlLayout(
  { left: 0, top: 0, width: 360, height: 640 },
  112,
  640,
);
assert.deepEqual(narrowLayout.dock, {
  left: 10,
  top: 10,
  width: 340,
  height: 112,
});
assert.equal(narrowLayout.stats.left, 10);
assert.equal(narrowLayout.stats.width, 340);
assert.equal(narrowLayout.settings.left, 10);
assert.equal(narrowLayout.settings.top, 130);
assert.equal(narrowLayout.settings.maxHeight, 500);
for (const panel of [
  narrowLayout.settings,
  narrowLayout.stats,
  narrowLayout.locator,
  narrowLayout.logs,
]) {
  assert.ok(panel.top >= narrowLayout.dock.top + narrowLayout.dock.height + 8);
  assert.ok(panel.left >= 8);
  assert.ok(panel.left + panel.width <= 350);
}
assert.equal(overlay.calculateControlLayout({}, 20, 720), null);

console.log("overlay-core tests passed");
