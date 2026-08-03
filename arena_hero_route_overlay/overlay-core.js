(function installArenaHeroOverlayCore(root, factory) {
  "use strict";

  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.ArenaHeroOverlayCore = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  const DEFAULT_SETTINGS = Object.freeze({
    showRoutes: true,
    showUnitLabels: true,
    showResources: true,
    lineWidth: 1.2,
    opacity: 0.42,
    workerColor: "#4f9f8a",
    vanguardColor: "#bd8754",
    rangerColor: "#6689ad",
    resourceColor: "#c5a54d",
  });

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function color(value, fallback) {
    return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value)
      ? value.toLowerCase()
      : fallback;
  }

  function normalizeSettings(value) {
    const source = value && typeof value === "object" ? value : {};
    const lineWidth = Number(source.lineWidth);
    const opacity = Number(source.opacity);
    return {
      showRoutes:
        typeof source.showRoutes === "boolean"
          ? source.showRoutes
          : DEFAULT_SETTINGS.showRoutes,
      showUnitLabels:
        typeof source.showUnitLabels === "boolean"
          ? source.showUnitLabels
          : DEFAULT_SETTINGS.showUnitLabels,
      showResources:
        typeof source.showResources === "boolean"
          ? source.showResources
          : DEFAULT_SETTINGS.showResources,
      lineWidth: Number.isFinite(lineWidth)
        ? clamp(lineWidth, 0.5, 5)
        : DEFAULT_SETTINGS.lineWidth,
      opacity: Number.isFinite(opacity)
        ? clamp(opacity, 0.1, 1)
        : DEFAULT_SETTINGS.opacity,
      workerColor: color(source.workerColor, DEFAULT_SETTINGS.workerColor),
      vanguardColor: color(source.vanguardColor, DEFAULT_SETTINGS.vanguardColor),
      rangerColor: color(source.rangerColor, DEFAULT_SETTINGS.rangerColor),
      resourceColor: color(source.resourceColor, DEFAULT_SETTINGS.resourceColor),
    };
  }

  function normalizePosition(value) {
    if (!Array.isArray(value) || value.length !== 2) {
      return null;
    }
    const x = Number(value[0]);
    const y = Number(value[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }

  function normalizeCamera(value) {
    if (!value || typeof value !== "object") {
      return null;
    }
    const x = Number(value.x);
    const y = Number(value.y);
    const cell = Number(value.cell);
    if (
      !Number.isFinite(x) ||
      !Number.isFinite(y) ||
      !Number.isFinite(cell) ||
      cell < 2 ||
      cell > 512
    ) {
      return null;
    }
    return { x, y, cell };
  }

  function gridToScreen(position, camera, width, height) {
    const point = normalizePosition(position);
    const view = normalizeCamera(camera);
    if (!point || !view) {
      return null;
    }
    return {
      x: width / 2 + (point[0] - view.x) * view.cell,
      y: height / 2 + (point[1] - view.y) * view.cell,
    };
  }

  function screenToGrid(x, y, camera, width, height) {
    const view = normalizeCamera(camera);
    if (!view || !Number.isFinite(x) || !Number.isFinite(y)) {
      return null;
    }
    return [
      Math.floor(view.x + (x - width / 2) / view.cell + 0.5),
      Math.floor(view.y + (y - height / 2) / view.cell + 0.5),
    ];
  }

  function pathTurnPoints(path) {
    if (!Array.isArray(path)) {
      return [];
    }
    const points = [];
    for (const value of path) {
      const point = normalizePosition(value);
      if (!point) {
        continue;
      }
      const previous = points[points.length - 1];
      if (!previous || previous[0] !== point[0] || previous[1] !== point[1]) {
        points.push(point);
      }
    }
    if (points.length <= 2) {
      return points;
    }
    const turns = [points[0]];
    for (let index = 1; index < points.length - 1; index += 1) {
      const before = points[index - 1];
      const current = points[index];
      const after = points[index + 1];
      const incoming = [current[0] - before[0], current[1] - before[1]];
      const outgoing = [after[0] - current[0], after[1] - current[1]];
      if (incoming[0] !== outgoing[0] || incoming[1] !== outgoing[1]) {
        turns.push(current);
      }
    }
    turns.push(points[points.length - 1]);
    return turns;
  }

  function reactFiber(element) {
    let current = element;
    for (let depth = 0; current && depth < 6; depth += 1) {
      for (const key of Object.getOwnPropertyNames(current)) {
        if (key.startsWith("__reactFiber$") || key.startsWith("__reactInternalInstance$")) {
          return current[key];
        }
      }
      current = current.parentElement;
    }
    return null;
  }

  function cameraCandidates(value, baseScore, output) {
    const direct = normalizeCamera(value);
    if (direct) {
      output.push({ camera: direct, score: baseScore });
    }
    if (!value || typeof value !== "object") {
      return;
    }
    for (const key of ["current", "camera", "viewport", "view"]) {
      const nested = normalizeCamera(value[key]);
      if (nested) {
        output.push({ camera: nested, score: baseScore - 2 });
      }
    }
  }

  function inspectFiber(fiber, depth, output) {
    let hook = fiber && fiber.memoizedState;
    const seenHooks = new Set();
    let hookIndex = 0;
    while (hook && typeof hook === "object" && !seenHooks.has(hook) && hookIndex < 64) {
      seenHooks.add(hook);
      const score = 100 - depth * 3 - hookIndex * 0.01;
      cameraCandidates(hook.memoizedState, score, output);
      cameraCandidates(hook.baseState, score - 1, output);
      if (hook.queue) {
        cameraCandidates(hook.queue.lastRenderedState, score - 1, output);
      }
      hook = hook.next;
      hookIndex += 1;
    }
    cameraCandidates(fiber && fiber.memoizedProps, 35 - depth, output);
    cameraCandidates(fiber && fiber.pendingProps, 30 - depth, output);
    cameraCandidates(fiber && fiber.stateNode && fiber.stateNode.state, 25 - depth, output);
  }

  function findCameraState(element) {
    const first = reactFiber(element);
    if (!first) {
      return null;
    }
    const queue = [{ fiber: first, depth: 0 }];
    const seenFibers = new Set();
    const candidates = [];
    while (queue.length && seenFibers.size < 160) {
      const { fiber, depth } = queue.shift();
      if (!fiber || seenFibers.has(fiber) || depth > 32) {
        continue;
      }
      seenFibers.add(fiber);
      inspectFiber(fiber, depth, candidates);
      queue.push({ fiber: fiber.return, depth: depth + 1 });
      queue.push({ fiber: fiber.alternate, depth });
    }
    candidates.sort((left, right) => right.score - left.score);
    return candidates.length ? candidates[0].camera : null;
  }

  return {
    DEFAULT_SETTINGS,
    findCameraState,
    gridToScreen,
    normalizeCamera,
    normalizePosition,
    normalizeSettings,
    pathTurnPoints,
    screenToGrid,
  };
});
