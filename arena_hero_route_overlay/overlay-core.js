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

  function calculateControlLayout(rect, dockHeight, viewportHeight) {
    const left = Number(rect && rect.left);
    const top = Number(rect && rect.top);
    const width = Number(rect && rect.width);
    const height = Number(rect && rect.height);
    if (
      !Number.isFinite(left) ||
      !Number.isFinite(top) ||
      !Number.isFinite(width) ||
      !Number.isFinite(height) ||
      width <= 0 ||
      height <= 0
    ) {
      return null;
    }
    const margin = 10;
    const bottom = top + height;
    const availableWidth = Math.max(1, width - margin * 2);
    const dockWidth = Math.min(760, availableWidth);
    const dockTop = Math.max(8, top + margin);
    const dockBottom = dockTop + Math.max(0, Number(dockHeight) || 0);
    const panelTop = dockBottom + 8;
    const visibleBottom = Math.min(
      bottom - margin,
      Number.isFinite(Number(viewportHeight))
        ? Number(viewportHeight) - 8
        : bottom - margin,
    );
    const maxPanelHeight = Math.max(120, Math.floor(visibleBottom - panelTop));

    const panel = (preferredWidth) => {
      const panelWidth = Math.min(preferredWidth, availableWidth);
      return {
        left: Math.max(8, left + margin),
        top: panelTop,
        width: panelWidth,
        maxHeight: maxPanelHeight,
      };
    };

    return {
      dock: {
        left: Math.max(8, left + margin),
        top: dockTop,
        width: dockWidth,
        height: Math.max(0, Number(dockHeight) || 0),
      },
      settings: panel(300),
      stats: panel(390),
      locator: panel(330),
      logs: panel(520),
    };
  }

  function normalizeLogs(value) {
    const source = value && typeof value === "object" ? value : {};
    const levels = new Set(["debug", "info", "success", "warning", "danger"]);
    const rawEntries = Array.isArray(source.entries) ? source.entries : [];
    const entries = [];
    for (const entry of rawEntries.slice(-250)) {
      if (!entry || typeof entry !== "object") {
        continue;
      }
      const tick = Number(entry.tick);
      if (
        !Number.isInteger(tick) ||
        tick < 0 ||
        typeof entry.event_id !== "string" ||
        typeof entry.title !== "string" ||
        typeof entry.message !== "string"
      ) {
        continue;
      }
      entries.push({
        version: 1,
        recorded_at: typeof entry.recorded_at === "string" ? entry.recorded_at.slice(0, 48) : null,
        tick,
        event_id: entry.event_id.slice(0, 160),
        source: typeof entry.source === "string" ? entry.source.slice(0, 24) : "server",
        category: typeof entry.category === "string" ? entry.category.slice(0, 32) : "系统",
        level: levels.has(entry.level) ? entry.level : "info",
        title: entry.title.slice(0, 96),
        message: entry.message.slice(0, 512),
        event_type: typeof entry.event_type === "string" ? entry.event_type.slice(0, 96) : null,
        reason_code: typeof entry.reason_code === "string" ? entry.reason_code.slice(0, 96) : null,
        position: normalizePosition(entry.position),
        actor: typeof entry.actor === "string" ? entry.actor.slice(0, 96) : null,
        target: typeof entry.target === "string" ? entry.target.slice(0, 96) : null,
      });
    }
    const latest = Number(source.latest_tick);
    return {
      version: 1,
      latest_tick: Number.isInteger(latest) && latest >= 0
        ? latest
        : entries.reduce((maximum, entry) => Math.max(maximum, entry.tick), 0),
      entries,
    };
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

  function gridDistance(from, to) {
    const origin = normalizePosition(from);
    const target = normalizePosition(to);
    if (!origin || !target) {
      return null;
    }
    return Math.abs(origin[0] - target[0]) + Math.abs(origin[1] - target[1]);
  }

  function estimateTravelMinutes(distance, secondsPerTick = 5) {
    const cells = Number(distance);
    const seconds = Number(secondsPerTick);
    if (!Number.isFinite(cells) || cells < 0 || !Number.isFinite(seconds) || seconds <= 0) {
      return null;
    }
    return (cells * seconds) / 60;
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

  function centerCameraOn(element, position) {
    const target = normalizePosition(position);
    const first = reactFiber(element);
    if (!target || !first) {
      return false;
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
      let hook = fiber.memoizedState;
      const seenHooks = new Set();
      let hookIndex = 0;
      while (
        hook &&
        typeof hook === "object" &&
        !seenHooks.has(hook) &&
        hookIndex < 64
      ) {
        seenHooks.add(hook);
        const camera = normalizeCamera(hook.memoizedState);
        const dispatch = hook.queue && hook.queue.dispatch;
        if (camera && typeof dispatch === "function") {
          candidates.push({
            camera,
            dispatch,
            score: 100 - depth * 3 - hookIndex * 0.01,
          });
        }
        hook = hook.next;
        hookIndex += 1;
      }
      queue.push({ fiber: fiber.return, depth: depth + 1 });
      queue.push({ fiber: fiber.alternate, depth });
    }
    candidates.sort((leftCandidate, rightCandidate) =>
      rightCandidate.score - leftCandidate.score
    );
    const candidate = candidates[0];
    if (!candidate) {
      return false;
    }
    try {
      candidate.dispatch((current) => ({
        ...(current && typeof current === "object" ? current : candidate.camera),
        x: target[0],
        y: target[1],
        cell: candidate.camera.cell,
      }));
      return true;
    } catch (error) {
      return false;
    }
  }

  const RESOURCE_KEY_RE = /(resource|mine|ore|mineral|crystal|deposit|resource.?node)/i;
  const RESOURCE_CONTAINER_KEYS = new Set([
    "resources",
    "resourceCells",
    "resource_cells",
    "resourceNodes",
    "resource_nodes",
    "mines",
    "minerals",
    "ore",
    "nodes",
  ]);

  function integerPosition(value) {
    const point = normalizePosition(value);
    return point && Number.isInteger(point[0]) && Number.isInteger(point[1])
      ? [point[0], point[1]]
      : null;
  }

  function resourceRecordPosition(value, keyHint) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    for (const key of ["position", "coordinates", "coordinate", "cell", "gridPosition", "grid_position", "tile", "location"]) {
      const point = integerPosition(value[key]);
      if (point) {
        const semantic = RESOURCE_KEY_RE.test(keyHint || "") ||
          ["resource", "resourceType", "resource_type", "kind", "type", "category", "name"].some((keyName) =>
            RESOURCE_KEY_RE.test(String(value[keyName] || ""))
          );
        if (semantic) {
          return point;
        }
      }
    }
    if (Number.isInteger(value.x) && Number.isInteger(value.y)) {
      const semantic = RESOURCE_KEY_RE.test(keyHint || "") ||
        ["resource", "resourceType", "resource_type", "kind", "type", "category", "name"].some((keyName) =>
          RESOURCE_KEY_RE.test(String(value[keyName] || ""))
        );
      if (semantic) {
        return [value.x, value.y];
      }
    }
    return null;
  }

  function collectResourceCells(value, keyHint, output, seen, budget, depth) {
    if (budget.count >= 6000 || depth > 8 || value === null || value === undefined) {
      return;
    }
    if (typeof value !== "object") {
      return;
    }
    if (seen.has(value)) {
      return;
    }
    seen.add(value);
    budget.count += 1;
    if (value instanceof Map) {
      for (const [key, item] of Array.from(value.entries()).slice(0, 4096)) {
        collectResourceCells(key, keyHint, output, seen, budget, depth + 1);
        collectResourceCells(item, keyHint, output, seen, budget, depth + 1);
      }
      return;
    }
    if (value instanceof Set) {
      for (const item of Array.from(value.values()).slice(0, 4096)) {
        collectResourceCells(item, keyHint, output, seen, budget, depth + 1);
      }
      return;
    }
    if (Array.isArray(value)) {
      if (RESOURCE_KEY_RE.test(keyHint || "")) {
        const direct = integerPosition(value);
        if (direct) {
          output.set(`${direct[0]},${direct[1]}`, direct);
          return;
        }
      }
      for (const item of value.slice(0, 4096)) {
        collectResourceCells(item, keyHint, output, seen, budget, depth + 1);
      }
      return;
    }
    const record = resourceRecordPosition(value, keyHint);
    if (record) {
      output.set(`${record[0]},${record[1]}`, record);
    }
    for (const [key, nested] of Object.entries(value)) {
      if (nested === null || nested === undefined || typeof nested !== "object") {
        continue;
      }
      const relevant = RESOURCE_KEY_RE.test(key) ||
        RESOURCE_CONTAINER_KEYS.has(key) ||
        key === "data" || key === "state" || key === "map" || key === "game" || key === "world";
      if (relevant || depth < 3) {
        collectResourceCells(nested, key, output, seen, budget, depth + 1);
      }
    }
  }

  function findResourceCells(element) {
    const first = reactFiber(element);
    if (!first) {
      return [];
    }
    const queue = [{ fiber: first, depth: 0 }];
    const seenFibers = new Set();
    const values = new Map();
    const seenValues = new Set();
    const budget = { count: 0 };
    while (queue.length && seenFibers.size < 160 && budget.count < 6000) {
      const { fiber, depth } = queue.shift();
      if (!fiber || seenFibers.has(fiber) || depth > 32) {
        continue;
      }
      seenFibers.add(fiber);
      collectResourceCells(fiber.memoizedProps, "props", values, seenValues, budget, 0);
      collectResourceCells(fiber.pendingProps, "props", values, seenValues, budget, 0);
      collectResourceCells(fiber.stateNode && fiber.stateNode.state, "state", values, seenValues, budget, 0);
      let hook = fiber.memoizedState;
      const seenHooks = new Set();
      let hookIndex = 0;
      while (hook && typeof hook === "object" && !seenHooks.has(hook) && hookIndex < 64) {
        seenHooks.add(hook);
        collectResourceCells(hook.memoizedState, "hook", values, seenValues, budget, 0);
        collectResourceCells(hook.baseState, "hook", values, seenValues, budget, 0);
        if (hook.queue) {
          collectResourceCells(hook.queue.lastRenderedState, "hook", values, seenValues, budget, 0);
        }
        hook = hook.next;
        hookIndex += 1;
      }
      queue.push({ fiber: fiber.return, depth: depth + 1 });
      queue.push({ fiber: fiber.alternate, depth });
    }
    return Array.from(values.values()).slice(0, 4096);
  }

  return {
    DEFAULT_SETTINGS,
    calculateControlLayout,
    centerCameraOn,
    findCameraState,
    findResourceCells,
    gridDistance,
    gridToScreen,
    estimateTravelMinutes,
    normalizeCamera,
    normalizeLogs,
    normalizePosition,
    normalizeSettings,
    pathTurnPoints,
    screenToGrid,
  };
});
