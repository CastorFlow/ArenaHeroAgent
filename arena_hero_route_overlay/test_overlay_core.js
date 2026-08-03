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

console.log("overlay-core tests passed");
