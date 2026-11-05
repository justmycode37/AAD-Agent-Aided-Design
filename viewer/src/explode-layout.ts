/**
 * CAD-style exploded *viewing* layout. Pure and deterministic: it maps visible
 * part bounds to world translations and leaves geometry, orientation and scale
 * alone. It is not a disassembly planner: it knows nothing about mates, contact,
 * fasteners or insertion paths, so a pose says nothing about how to take a real
 * assembly apart.
 *
 * Strategy, in the spirit of SOLIDWORKS "auto-spacing along an axis":
 *  1. Take the largest global extent as the assembly axis.
 *  2. Cluster parts into coaxial stacks (shared perpendicular center). Circular
 *     patterns such as bearing balls stay together as one rigid ring item.
 *  3. In each stack the longest item stays put; the others leave along the axis,
 *     keeping their original order, split about evenly between the two ends.
 *  4. Place whole stacks in the perpendicular plane at the closest free spot to
 *     their radially pushed-out position, so stacks form parallel lanes.
 *
 * Every pair of final world AABBs is separated by at least `gap` on some axis.
 * Their projections on the other axes may still overlap, as in a CAD exploded view.
 *
 * Wiring to three-cad-viewer: `viewer.explode()` multiplies bbox centers, so skip
 * it and build the same animation from these offsets with `clearAnimation`, one
 * `addPositionTrack` per leaf, then `initAnimation`. Offsets are world vectors, so
 * convert each with the leaf parent's inverse transform,
 * `parent.worldToLocal(origin + delta) - parent.worldToLocal(origin)`, before adding
 * it to the leaf's local position; that handles a rotated or scaled parent.
 */

export type Vec3 = [number, number, number];

export interface ExplodePart {
  readonly id: string;
  readonly min: readonly [number, number, number];
  readonly max: readonly [number, number, number];
}

export interface ExplodeLayout {
  /** Assembly axis: 0 = X, 1 = Y, 2 = Z. */
  axis: 0 | 1 | 2;
  /** Guaranteed minimum separation between final world AABBs, in model units. */
  gap: number;
  /** World translation for every input id. */
  offsets: Map<string, Vec3>;
}

/** Minimum AABB separation, as a fraction of the largest global extent. */
const GAP_RATIO = 0.015;
/** Perpendicular centers this close (fraction of largest extent) count as coaxial. */
const COAXIAL_RATIO = 0.01;
/** Radial and axial push away from the assembly, so already-clear parts still separate. */
const SPREAD = 1.25;
/** Margin over `gap` so rounding cannot break the guarantee. */
const PLAN_MARGIN = 1 + 1e-6;
/** Ring radii within this fraction of each other count as one circular pattern. */
const RING_RADIUS_TOLERANCE = 0.05;
/** A pattern that must grow more than this to clear itself is not treated as a ring. */
const RING_MAX_EXPANSION = 2;
/** A part whose AABB fills this share of the global AABB is treated as an enclosure. */
const ENCLOSURE_VOLUME_RATIO = 0.25;

interface Piece {
  id: string;
  /** Perpendicular center and half sizes. */
  u: number; v: number; hu: number; hv: number;
  amin: number; amax: number;
  /** Fraction of the global AABB volume. */
  fill: number;
}

/** One rigid axial unit: a single part or a circular pattern. */
interface Item {
  pieces: Piece[];
  /** Radial translation per piece, parallel to `pieces`. */
  du: number[]; dv: number[];
  umin: number; umax: number; vmin: number; vmax: number;
  amin: number; amax: number;
  /** Assembled radial size, for nesting order: inner parts leave next to the anchor. */
  footprint: number;
  /** Axial translation chosen by the stack layout. */
  shift: number;
}

interface Stack {
  u: number; v: number;
  items: Item[];
}

/** Placed box in layout coordinates. */
interface Box {
  umin: number; umax: number; vmin: number; vmax: number;
  amin: number; amax: number;
}

export function planExplodeLayout(parts: readonly ExplodePart[]): ExplodeLayout {
  const sorted = validated(parts);
  const offsets = new Map<string, Vec3>();
  if (sorted.length === 0) return { axis: 2, gap: 0, offsets };

  const gmin = [0, 1, 2].map(i => sorted.reduce((low, part) => Math.min(low, part.min[i]), Infinity));
  const gmax = [0, 1, 2].map(i => sorted.reduce((high, part) => Math.max(high, part.max[i]), -Infinity));
  const extent = [0, 1, 2].map(i => gmax[i] - gmin[i]);
  const unit = Math.max(...extent) || 1;
  // Near-ties go to Z, then Y, so a cube-like model does not flip axis on noise.
  const axis = ([2, 1, 0] as const).reduce((best, i) => (extent[i] > extent[best] * 1.01 ? i : best), 2 as 0 | 1 | 2);
  const [ua, va] = axis === 2 ? [0, 1] : axis === 1 ? [0, 2] : [1, 2];
  const gap = GAP_RATIO * unit;
  const planGap = gap * PLAN_MARGIN;
  const tolerance = COAXIAL_RATIO * unit;

  const pieces = sorted.map((part): Piece => ({
    id: part.id,
    u: (part.min[ua] + part.max[ua]) / 2, v: (part.min[va] + part.max[va]) / 2,
    hu: (part.max[ua] - part.min[ua]) / 2, hv: (part.max[va] - part.min[va]) / 2,
    amin: part.min[axis], amax: part.max[axis],
    fill: [0, 1, 2].reduce((fill, i) => (extent[i] > 0 ? fill * (part.max[i] - part.min[i]) / extent[i] : fill), 1),
  }));

  const stacks = buildStacks(pieces, tolerance, planGap);
  for (const stack of stacks) layoutStack(stack, tolerance, planGap);
  const center = { u: (gmin[ua] + gmax[ua]) / 2, v: (gmin[va] + gmax[va]) / 2 };
  const placed = placeStacks(stacks, center, planGap);

  stacks.forEach((stack, index) => {
    for (const item of stack.items) {
      item.pieces.forEach((piece, i) => {
        const offset: Vec3 = [0, 0, 0];
        offset[ua] = placed[index].u + item.du[i];
        offset[va] = placed[index].v + item.dv[i];
        offset[axis] = item.shift;
        offsets.set(piece.id, offset);
      });
    }
  });
  return { axis, gap, offsets };
}

function validated(parts: readonly ExplodePart[]): ExplodePart[] {
  const seen = new Set<string>();
  for (const part of parts) {
    if (seen.has(part.id)) throw new Error(`Duplicate part id: ${part.id}`);
    seen.add(part.id);
    for (let i = 0; i < 3; i++) {
      if (!Number.isFinite(part.min[i]) || !Number.isFinite(part.max[i]) || part.min[i] > part.max[i]) {
        throw new Error(`Invalid bounds for part: ${part.id}`);
      }
    }
  }
  // Layout must not depend on the order parts arrive in.
  return [...parts].sort((a, b) => compareText(a.id, b.id));
}

const compareText = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

function singleItem(piece: Piece): Item {
  return {
    pieces: [piece], du: [0], dv: [0],
    umin: piece.u - piece.hu, umax: piece.u + piece.hu,
    vmin: piece.v - piece.hv, vmax: piece.v + piece.hv,
    amin: piece.amin, amax: piece.amax,
    footprint: 2 * Math.max(piece.hu, piece.hv),
    shift: 0,
  };
}

function buildStacks(pieces: Piece[], tolerance: number, gap: number): Stack[] {
  // Fixed seeds, longest first, so a shaft defines its axis and clusters cannot chain.
  const bySeedOrder = [...pieces].sort((a, b) => (b.amax - b.amin) - (a.amax - a.amin) || compareText(a.id, b.id));
  const cell = tolerance;
  const grid = new Map<string, number[]>();
  const clusters: { u: number; v: number; pieces: Piece[] }[] = [];
  for (const piece of bySeedOrder) {
    const cu = Math.floor(piece.u / cell);
    const cv = Math.floor(piece.v / cell);
    let found: number | undefined;
    for (let i = cu - 1; i <= cu + 1; i++) {
      for (let j = cv - 1; j <= cv + 1; j++) {
        for (const index of grid.get(`${i},${j}`) ?? []) {
          const cluster = clusters[index];
          if (Math.hypot(cluster.u - piece.u, cluster.v - piece.v) <= tolerance && (found === undefined || index < found)) found = index;
        }
      }
    }
    if (found !== undefined) {
      clusters[found].pieces.push(piece);
      continue;
    }
    const key = `${cu},${cv}`;
    const occupants = grid.get(key);
    if (occupants) occupants.push(clusters.length);
    else grid.set(key, [clusters.length]);
    clusters.push({ u: piece.u, v: piece.v, pieces: [piece] });
  }

  const rings = findRings(clusters, tolerance, gap);
  const stacks: Stack[] = [];
  const stackAt = new Map<object, Stack>();
  for (const cluster of clusters) {
    const loose = cluster.pieces.filter(piece => !rings.claimed.has(piece));
    if (loose.length === 0) continue;
    const stack: Stack = { u: cluster.u, v: cluster.v, items: loose.map(singleItem) };
    stackAt.set(cluster, stack);
    stacks.push(stack);
  }
  for (const ring of rings.found) {
    const owner = ring.cluster && stackAt.get(ring.cluster);
    if (owner) owner.items.push(ring.item);
    else stacks.push({ u: ring.u, v: ring.v, items: [ring.item] });
  }
  return stacks;
}

interface Ring { cluster?: object; u: number; v: number; item: Item }

/**
 * Find circular patterns: at least three same-size parts at one axial station,
 * equidistant from an axis. They move as a unit, like balls staying with their race,
 * rather than scattering into a lane each.
 */
function findRings(clusters: { u: number; v: number; pieces: Piece[] }[], tolerance: number, gap: number) {
  const buckets = new Map<string, Piece[]>();
  for (const cluster of clusters) {
    for (const piece of cluster.pieces) {
      const key = [piece.hu * 2, piece.hv * 2, piece.amax - piece.amin, (piece.amin + piece.amax) / 2].map(x => Math.round(x / tolerance)).join();
      const bucket = buckets.get(key);
      if (bucket) bucket.push(piece);
      else buckets.set(key, [piece]);
    }
  }
  const patterned = new Set<Piece>();
  for (const bucket of buckets.values()) if (bucket.length >= 3) for (const piece of bucket) patterned.add(piece);
  const axes = clusters.filter(cluster => cluster.pieces.some(piece => !patterned.has(piece)));
  axes.sort((a, b) => b.pieces.length - a.pieces.length);

  const claimed = new Set<Piece>();
  const found: Ring[] = [];
  const tryPattern = (members: Piece[], u: number, v: number, cluster?: object) => {
    const free = members.filter(piece => !claimed.has(piece))
      .map(piece => ({ piece, radius: Math.hypot(piece.u - u, piece.v - v) }))
      .filter(entry => entry.radius > tolerance)
      .sort((a, b) => a.radius - b.radius || compareText(a.piece.id, b.piece.id));
    for (let start = 0; start < free.length;) {
      let end = start + 1;
      while (end < free.length && free[end].radius - free[start].radius <= Math.max(tolerance, free[start].radius * RING_RADIUS_TOLERANCE)) end++;
      const run = free.slice(start, end).map(entry => entry.piece);
      start = end;
      if (run.length < 3) continue;
      const cu = run.reduce((sum, piece) => sum + piece.u, 0) / run.length;
      const cv = run.reduce((sum, piece) => sum + piece.v, 0) / run.length;
      if (Math.hypot(cu - u, cv - v) > tolerance) continue;
      const item = ringItem(run, u, v, gap);
      if (!item) continue;
      for (const piece of run) claimed.add(piece);
      found.push({ cluster, u, v, item });
    }
  };
  const groups = [...buckets.values()].filter(bucket => bucket.length >= 3);
  for (const bucket of groups) {
    for (const axisCluster of axes) tryPattern(bucket, axisCluster.u, axisCluster.v, axisCluster);
    const rest = bucket.filter(piece => !claimed.has(piece));
    if (rest.length >= 3) {
      tryPattern(rest, rest.reduce((sum, piece) => sum + piece.u, 0) / rest.length, rest.reduce((sum, piece) => sum + piece.v, 0) / rest.length);
    }
  }
  return { claimed, found };
}

/** Spread a pattern radially just enough for its members to clear each other. */
function ringItem(run: Piece[], u: number, v: number, gap: number): Item | undefined {
  let scale = 1;
  for (let i = 0; i < run.length; i++) {
    for (let j = i + 1; j < run.length; j++) {
      const a = run[i], b = run[j];
      const du = Math.abs(a.u - b.u), dv = Math.abs(a.v - b.v);
      const needU = du > 0 ? (a.hu + b.hu + gap) / du : Infinity;
      const needV = dv > 0 ? (a.hv + b.hv + gap) / dv : Infinity;
      scale = Math.max(scale, Math.min(needU, needV));
      if (scale > RING_MAX_EXPANSION) return undefined;
    }
  }
  const du = run.map(piece => (scale - 1) * (piece.u - u));
  const dv = run.map(piece => (scale - 1) * (piece.v - v));
  return {
    pieces: run, du, dv,
    umin: Math.min(...run.map((piece, i) => piece.u + du[i] - piece.hu)),
    umax: Math.max(...run.map((piece, i) => piece.u + du[i] + piece.hu)),
    vmin: Math.min(...run.map((piece, i) => piece.v + dv[i] - piece.hv)),
    vmax: Math.max(...run.map((piece, i) => piece.v + dv[i] + piece.hv)),
    amin: Math.min(...run.map(piece => piece.amin)),
    amax: Math.max(...run.map(piece => piece.amax)),
    footprint: 2 * Math.max(...run.map(piece => Math.hypot(piece.u - u, piece.v - v) + Math.max(piece.hu, piece.hv))),
    shift: 0,
  };
}

const itemCenter = (item: Item) => (item.amin + item.amax) / 2;
const itemLength = (item: Item) => item.amax - item.amin;
const itemName = (item: Item) => item.pieces[0].id;

/** Slide items off the stack's longest item along the axis, preserving axial order. */
function layoutStack(stack: Stack, tolerance: number, gap: number): void {
  const anchor = [...stack.items].sort((a, b) => itemLength(b) - itemLength(a) || b.footprint - a.footprint || compareText(itemName(a), itemName(b)))[0];
  // Parts at one station order by nesting, not by rounding noise in their centers.
  const station = (item: Item) => Math.round(itemCenter(item) / tolerance);
  const outward = (a: Item, b: Item, sign: number) =>
    sign * (station(a) - station(b)) || a.footprint - b.footprint || compareText(itemName(a), itemName(b));
  const rest = stack.items.filter(item => item !== anchor).sort((a, b) => outward(a, b, 1));
  const split = chooseSplit(rest, anchor, tolerance);
  const anchorCenter = itemCenter(anchor);

  let cursor = anchor.amax;
  for (const item of rest.slice(split).sort((a, b) => outward(a, b, 1))) {
    const start = Math.max(anchorCenter + SPREAD * (item.amin - anchorCenter), cursor + gap);
    item.shift = start - item.amin;
    cursor = start + itemLength(item);
  }
  cursor = anchor.amin;
  for (const item of rest.slice(0, split).sort((a, b) => outward(a, b, -1))) {
    const end = Math.min(anchorCenter + SPREAD * (item.amax - anchorCenter), cursor - gap);
    item.shift = end - item.amax;
    cursor = end - itemLength(item);
  }
}

/**
 * Pick how many of the axially sorted items go to the low end. Balance both ends so
 * the stack does not grow one-sidedly, keep same-station items (a hub and its sleeve)
 * together, and never send a part across an anchor it already clears.
 */
function chooseSplit(rest: Item[], anchor: Item, tolerance: number): number {
  const count = rest.length;
  const anchorCenter = itemCenter(anchor);
  const natural = rest.filter(item => itemCenter(item) < anchorCenter).length;
  let lowest = 0;
  let highest = count;
  rest.forEach((item, index) => {
    if (item.amax <= anchor.amin) lowest = Math.max(lowest, index + 1);
    if (item.amin >= anchor.amax) highest = Math.min(highest, index);
  });
  if (lowest > highest) return natural;
  const total = rest.reduce((sum, item) => sum + itemLength(item), 0);
  let best = natural >= lowest && natural <= highest ? natural : lowest;
  let bestCost = Infinity;
  let low = 0;
  for (let split = 0; split <= count; split++) {
    const atStation = split > 0 && split < count && itemCenter(rest[split]) - itemCenter(rest[split - 1]) <= tolerance;
    if (split >= lowest && split <= highest && !atStation) {
      const cost = Math.max(low, total - low);
      if (cost < bestCost || (cost === bestCost && Math.abs(split - natural) < Math.abs(best - natural))) {
        best = split;
        bestCost = cost;
      }
    }
    if (split < count) low += itemLength(rest[split]);
  }
  return best;
}

function stackBox(stack: Stack): Box {
  const box: Box = { umin: Infinity, umax: -Infinity, vmin: Infinity, vmax: -Infinity, amin: Infinity, amax: -Infinity };
  for (const item of stack.items) {
    box.umin = Math.min(box.umin, item.umin);
    box.umax = Math.max(box.umax, item.umax);
    box.vmin = Math.min(box.vmin, item.vmin);
    box.vmax = Math.max(box.vmax, item.vmax);
    box.amin = Math.min(box.amin, item.amin + item.shift);
    box.amax = Math.max(box.amax, item.amax + item.shift);
  }
  return box;
}

/** Inner coaxial stacks claim the middle, then loose parts, then enclosures go around them. */
function placementTier(stack: Stack): number {
  if (stack.items.some(item => item.pieces.some(piece => piece.fill >= ENCLOSURE_VOLUME_RATIO))) return 2;
  return stack.items.length > 1 ? 0 : 1;
}

function placeStacks(stacks: Stack[], center: { u: number; v: number }, gap: number): { u: number; v: number }[] {
  const boxes = stacks.map(stackBox);
  const radial = boxes.map(box => Math.hypot((box.umin + box.umax) / 2 - center.u, (box.vmin + box.vmax) / 2 - center.v));
  const tiers = stacks.map(placementTier);
  const order = stacks.map((_, index) => index).sort((a, b) =>
    tiers[a] - tiers[b] || radial[a] - radial[b] || compareText(itemName(stacks[a].items[0]), itemName(stacks[b].items[0])));

  const result = stacks.map(() => ({ u: 0, v: 0 }));
  const placed: Box[] = [];
  for (const index of order) {
    const box = boxes[index];
    const cu = (box.umin + box.umax) / 2;
    const cv = (box.vmin + box.vmax) / 2;
    const hu = (box.umax - box.umin) / 2;
    const hv = (box.vmax - box.vmin) / 2;
    const spot = closestFree(center.u + SPREAD * (cu - center.u), center.v + SPREAD * (cv - center.v), hu, hv, box, placed, gap);
    result[index] = { u: spot.u - cu, v: spot.v - cv };
    placed.push({ umin: spot.u - hu, umax: spot.u + hu, vmin: spot.v - hv, vmax: spot.v + hv, amin: box.amin, amax: box.amax });
  }
  return result;
}

interface Rect { u0: number; u1: number; v0: number; v1: number }

/**
 * Closest center to (du, dv) where a box clears all placed boxes it shares axial range
 * with. Each placed box forbids an open rectangle of centers. The optimum lies on the
 * row v = dv or on a rectangle's v edge, so scanning those rows nearest-first and
 * stopping once a row is farther than the best hit is exact.
 */
function closestFree(du: number, dv: number, hu: number, hv: number, box: Box, placed: Box[], gap: number): { u: number; v: number } {
  let rects: Rect[] = [];
  for (const other of placed) {
    if (other.amax + gap <= box.amin || other.amin - gap >= box.amax) continue;
    rects.push({ u0: other.umin - hu - gap, u1: other.umax + hu + gap, v0: other.vmin - hv - gap, v1: other.vmax + hv + gap });
  }
  let bestU = freeOnRow(du, dv, rects);
  let bestV = dv;
  let best = Math.abs(bestU - du);
  // Rectangles beyond the first hit cannot block anything better.
  rects = rects.filter(r => r.u0 < du + best && r.u1 > du - best && r.v0 < dv + best && r.v1 > dv - best);
  const rows = rects.flatMap(r => [r.v0, r.v1]).sort((a, b) => Math.abs(a - dv) - Math.abs(b - dv) || b - a);
  for (const row of rows) {
    const away = Math.abs(row - dv);
    if (away >= best) break;
    const u = freeOnRow(du, row, rects);
    const distance = Math.hypot(u - du, away);
    if (distance < best) {
      best = distance;
      bestU = u;
      bestV = row;
    }
  }
  return { u: bestU, v: bestV };
}

/** Closest u to `du` on a row that no rectangle covers. */
function freeOnRow(du: number, row: number, rects: Rect[]): number {
  const spans = rects.filter(r => r.v0 < row && row < r.v1).sort((a, b) => a.u0 - b.u0);
  let index = 0;
  while (index < spans.length) {
    const low = spans[index].u0;
    let high = spans[index].u1;
    for (index++; index < spans.length && spans[index].u0 < high; index++) high = Math.max(high, spans[index].u1);
    if (low >= du) return du;
    if (high > du) return du - low < high - du ? low : high;
  }
  return du;
}
