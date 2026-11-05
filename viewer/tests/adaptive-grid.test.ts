import { describe, expect, it } from 'vitest';
import { gridCoordinate, gridDivisions, overlaps } from '../src/adaptive-grid';

describe('adaptive coordinate grid', () => {
  it('refines a fixed extent as zoom increases without moving its planes', () => {
    expect([.1, .3, 1, 3].map(ppu => gridDivisions(1200, ppu))).toEqual([1, 4, 12, 40]);
  });
  it('holds the interval through small zoom changes rather than flickering at thresholds', () => {
    expect(gridDivisions(1200, .3, 4)).toBe(4);
    expect(gridDivisions(1200, .25, 4)).toBe(4);
    expect(gridDivisions(1200, .5, 4)).toBe(4);
    expect(gridDivisions(1200, .6, 4)).toBeGreaterThan(4);
    expect(gridDivisions(1200, .1, 4)).toBeLessThan(4);
  });
  it('preserves fractional and panned world coordinates without native four-decimal rounding', () => {
    expect(gridCoordinate(.000012, 0)).toBe('0.000012');
    expect(gridCoordinate(.000013, 0)).toBe('0.000013');
    expect(gridCoordinate(50, 200)).toBe('250');
    expect(gridCoordinate(.1, .2)).toBe('0.3');
  });
  it('leaves room between numbers and catches duplicates across grid planes', () => {
    const a = { x: 100, y: 100, width: 48, height: 11 };
    expect(overlaps(a, a)).toBe(true);
    expect(overlaps(a, { ...a, x: 150 })).toBe(true);
    expect(overlaps(a, { ...a, x: 185 })).toBe(false);
    expect(overlaps(a, { ...a, y: 130 })).toBe(false);
  });
});
