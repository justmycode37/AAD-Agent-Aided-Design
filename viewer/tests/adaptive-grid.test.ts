import { describe, expect, it } from 'vitest';
import { gridCoordinate, gridCoverage, gridInterval, overlaps } from '../src/adaptive-grid';

describe('adaptive coordinate grid', () => {
  it('continues coarsening beyond the original model bounds on zoom out', () => {
    const levels = [1, .1, .01, .001].map(ppu => gridCoverage(160, ppu, 900));
    expect(levels.map(x => x.step)).toEqual([100, 1000, 10000, 100000]);
    for (let i = 0; i < levels.length; i++) {
      expect(levels[i].half * [1, .1, .01, .001][i]).toBeGreaterThanOrEqual(720);
      expect(levels[i].divisions).toBeLessThanOrEqual(128);
    }
  });
  it('holds the interval through small zoom changes rather than flickering at thresholds', () => {
    expect(gridInterval(1, 100)).toBe(100);
    expect(gridInterval(.65, 100)).toBe(100);
    expect(gridInterval(1.5, 100)).toBe(100);
    expect(gridInterval(1.6, 100)).toBeLessThan(100);
    expect(gridInterval(.6, 100)).toBeGreaterThan(100);
  });
  it('retains coverage through small back-and-forth zoom movements', () => {
    const initial = gridCoverage(160, 1, 900);
    expect(gridCoverage(160, .99, 900, 0, initial.step, initial.half)).toEqual(initial);
    expect(gridCoverage(160, 1.01, 900, 0, initial.step, initial.half)).toEqual(initial);
    expect(gridCoverage(160, .1, 900, 0, initial.step, initial.half).half).toBeGreaterThan(initial.half);
  });
  it('does not change its initial interval on the next identical frame', () => {
    for (const ppu of [.01, .1, .3, .33, .56, .65, 1, 1.6, 4, 1000]) {
      const initial = gridCoverage(160, ppu, 900);
      expect(gridCoverage(160, ppu, 900, 0, initial.step, initial.half)).toEqual(initial);
    }
  });
  it('bounds allocation when panned far from the origin or zoomed into a detail', () => {
    expect(gridCoverage(160, 10000, 900).divisions).toBeLessThanOrEqual(128);
    expect(gridCoverage(160, 10, 900, 1000000).divisions).toBeLessThanOrEqual(128);
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
