import { describe, expect, it } from 'vitest';
import { gridCoordinate, gridStep, niceGridStep, overlaps } from '../src/adaptive-grid';

describe('adaptive coordinate grid', () => {
  it('uses round intervals for large and fractional models', () => {
    expect([0.0021, 0.06, 1, 1.1, 4.5, 50, 201].map(niceGridStep)).toEqual([0.005, .1, 1, 2, 5, 50, 500]);
  });
  it('gets coarser on zoom out, finer on zoom in, and bounds allocation at extreme zoom', () => {
    const levels = [.05, .2, 1, 3, 10, 100000].map(ppu => gridStep(ppu, 500));
    expect(levels).toEqual([2000, 500, 100, 50, 10, .001]);
    for (let i = 1; i < levels.length; i++) expect(levels[i]).toBeLessThanOrEqual(levels[i - 1]);
    expect(gridStep(1, 8000)).toBeGreaterThanOrEqual(8000 / 32);
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
