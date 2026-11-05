"""Fit the housing's physical parameters to TUNING silhouettes only. Outputs a parameter table, never an outline.
usage: hullfit.py masksdir init_result.json out.json [sweeps]"""
import json, sys, time, numpy as np, cv2, vtk
from vtk.util import numpy_support as ns
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation as Rn
TUNING = ('user_photo', 'p12', 'p27', 'p20')
WEIGHTS = dict(user_photo=1., p12=1., p27=3., p20=3.)
ZS = np.array([0, 14, 40, 70, 100, 130, 165, 200, 240, 280, 320, 370, 420, 470, 520, 570, 620, 660, 700, 740, 780, 820, 860, 900, 940, 985.])
N = 48; CY = -45.0
_Z0 = [0, 14, 60, 120, 170, 230, 320, 420, 520, 620, 700, 770, 840, 900, 950, 985.]
I0 = np.interp(ZS, _Z0, [184.0, 180.0, 172.0, 164.0, 143.0, 130.0, 122.0, 100.0, 86.0, 74.0, 68.0, 62.0, 43.0, 24.0, 18.0, 10.0])
O0 = np.interp(ZS, _Z0, [162.0, 152.0, 139.0, 126.0, 114.0, 106.0, 102.0, 106.0, 111.0, 105.0, 99.0, 94.0, 79.0, 71.0, 62.0, 53.0])
D0 = np.zeros(len(ZS))  # lateral offset of the output-side wall circle
AX = np.array([[0, 0.], [0, -89.]])
P0 = dict(pan_x0=-145.5, pan_x1=84.5, pan_y=-227.9, pan_z0=139., pan_z1=499.,
          fd_y=-186., fd_z=70.4, fd_r=62., tube_x=-185., tube_r=27., flange_r=56., boss_x=154., boss_r=74.,
          shaft_r=24., shaft_end=1044.5, side_w=0., side_y=-60., side_z0=60., side_z1=300.,
          top_x=-40., top_y=110., top_r=40., top_z0=70., top_z1=320., tail_x=30., tail_y=-30., tail_r=30., tail_z0=700., tail_z1=860.,
          fr_x=62., fr_y=-151., fr_r=50., fr_z0=130., fr_z1=830., bell_x=0., bell_y=0.,
          l1_x=120., l1_y=120., l1_r=20., l2_x=-120., l2_y=120., l2_r=20., l3_x=170., l3_y=-90., l3_r=20., l4_x=-170., l4_y=-90., l4_r=20., l5_x=0., l5_y=-175., l5_r=20.,
          pan_ch=70.,
          **{f'b{i}_{k}': v for i, (a_, z_) in enumerate([(70, 180), (95, 240), (120, 320), (60, 420), (100, 560), (40, 700), (130, 800), (-20, 860)]) for k, v in (('a', float(a_)), ('z', float(z_)), ('r', 16.), ('h', 4.))})
FLOOR = True
RAILS = [(78., -18.), (74., 26.), (-88., -80.), (88., -70.)]
FRONT = (64., -160.)
def floor_ri(z):
    if z <= 100: return 151.
    if z <= 155: return 118.
    if z <= 250: return 56.
    if z <= 530: return 96. if 455 <= z <= 495 else 66.
    return 72. if z <= 740 else 50. if z <= 760 else .1
def floor_ro(z):
    if z < 240: return 30.
    if z <= 735: return 80.
    if z <= 790: return 58.
    if z <= 955: return 78.
    return 49.
def cyl_tris(c0, c1, r, n=24):
    c0, c1 = np.array(c0, float), np.array(c1, float); d = c1 - c0; d /= np.linalg.norm(d); a = np.array([1, 0, 0.]) if abs(d[0]) < .9 else np.array([0, 1, 0.])
    u = np.cross(d, a); u /= np.linalg.norm(u); w = np.cross(d, u); th = 2 * np.pi * np.arange(n) / n; ring = r * (np.cos(th)[:, None] * u + np.sin(th)[:, None] * w)
    V = np.vstack([c0 + ring, c1 + ring, c0, c1]); F = []
    for i in range(n):
        j = (i + 1) % n; F += [[i, j, n + j], [i, n + j, n + i], [2 * n, j, i], [2 * n + 1, n + i, n + j]]
    return V, np.array(F)
def box_tris(x0, x1, y0, y1, z0, z1):
    V = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float)
    F = np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]]); return V, F
def hull_section(ri, ro, dxo, z, d):
    """Radial hull about (0, CY) from wall circles around the shafts plus named lobes, all with physical meaning."""
    th = 2 * np.pi * np.arange(N) / N; dx, dy = np.cos(th), np.sin(th); R = np.zeros(N)
    bx, by = (d['bell_x'], d['bell_y']) if z <= 70 else (0., 0.)
    fo = floor_ro(z); dxo_ = dxo if ro >= fo else 0.; ri = max(ri, floor_ri(z)); ro = max(ro, fo)
    circles = [(bx, by - CY, ri), (dxo_, -89. - CY, ro)]
    if 296 <= z <= 740: circles += [(x_, y_ - CY, 18.) for x_, y_ in RAILS]
    if d['top_z0'] <= z <= d['top_z1']: circles.append((d['top_x'], d['top_y'] - CY, d['top_r']))
    if d['tail_z0'] <= z <= d['tail_z1']: circles.append((d['tail_x'], d['tail_y'] - CY, d['tail_r']))
    if min(d['fr_z0'], 100.) <= z <= max(d['fr_z1'], 820.): circles.append((FRONT[0], FRONT[1] - CY, max(d['fr_r'], 50.)))
    if z <= 14: circles += [(d[f'l{i}_x'], d[f'l{i}_y'] - CY, d[f'l{i}_r']) for i in range(1, 6)]
    # Support function sampled densely, then ray intersect through the convex hull.
    ang = 2 * np.pi * np.arange(180) / 180; pts = np.vstack([np.c_[c[0] + c[2] * np.cos(ang), c[1] + c[2] * np.sin(ang)] for c in circles])
    hull = cv2.convexHull(pts.astype(np.float32))[:, 0, :].astype(float)
    a, b = hull, np.roll(hull, -1, 0); e = b - a
    for k in range(N):
        den = dx[k] * e[:, 1] - dy[k] * e[:, 0]; ok = np.abs(den) > 1e-9
        t = np.where(ok, (a[:, 0] * e[:, 1] - a[:, 1] * e[:, 0]) / np.where(ok, den, 1), -1); u = np.where(ok, (a[:, 0] * dy[k] - a[:, 1] * dx[k]) / np.where(ok, den, 1), -1)
        v = t[(t >= 0) & (u >= -1e-6) & (u <= 1 + 1e-6)]; R[k] = v.max() if len(v) else ri
    return np.c_[R * dx, CY + R * dy]
BOUNDS = dict(pan_x0=(-150, -90), pan_x1=(30, 110), pan_y=(-245, -190), pan_z0=(120, 240), pan_z1=(440, 600), pan_ch=(20, 90),
              fd_y=(-205, -165), fd_z=(62, 110), tube_x=(-240, -150), tube_r=(22, 34), flange_r=(45, 70), boss_x=(110, 190), boss_r=(58, 82),
              shaft_end=(1000, 1110), top_x=(-70, 70), top_y=(60, 125), top_r=(20, 45), top_z0=(40, 200), top_z1=(200, 420),
              tail_x=(-60, 60), tail_y=(-70, 10), tail_r=(15, 40), tail_z0=(650, 800), tail_z1=(780, 900),
              bell_x=(-12, 12), bell_y=(-12, 12), fr_r=(45, 62), fr_z0=(90, 200), fr_z1=(780, 880))
BOUNDS.update({f'l{i}_r': (10, 24) for i in range(1, 6)}); BOUNDS.update({f'b{i}_r': (6, 26) for i in range(8)}); BOUNDS.update({f'b{i}_h': (-6, 14) for i in range(8)})
def within(x):
    n = len(ZS); d = dict(zip(P0, x[3 * n:]))
    if any(not (lo <= d[k] <= hi) for k, (lo, hi) in BOUNDS.items()): return False
    return all(145 <= np.hypot(d[f'l{i}_x'], d[f'l{i}_y']) <= 214 for i in range(1, 6)) and np.abs(x[2 * n:3 * n]).max() <= 15
def clip_all(x):
    n = len(ZS); keys = list(P0); x = x.copy()
    for k, (lo, hi) in BOUNDS.items(): x[3 * n + keys.index(k)] = np.clip(x[3 * n + keys.index(k)], lo, hi)
    for i in range(1, 6):
        ix, iy = 3 * n + keys.index(f'l{i}_x'), 3 * n + keys.index(f'l{i}_y'); rad = np.hypot(x[ix], x[iy]); rad2 = np.clip(rad, 146, 213); x[ix] *= rad2 / max(rad, 1e-6); x[iy] *= rad2 / max(rad, 1e-6)
    x[2 * n:3 * n] = np.clip(x[2 * n:3 * n], -14, 14); return x
def build(x):
    n = len(ZS); ri, ro, dxo = x[:n], x[n:2 * n], x[2 * n:3 * n]; d = dict(zip(P0, x[3 * n:]))
    V, F = [], []; base = 0
    ring = []
    for k in range(n):
        ring.append(np.c_[hull_section(ri[k], ro[k], dxo[k], ZS[k], d), np.full(N, ZS[k])])
    W = np.vstack(ring)
    for k in range(n - 1):
        for i in range(N):
            j = (i + 1) % N; a, b, c, e = k * N + i, k * N + j, (k + 1) * N + j, (k + 1) * N + i; F += [[a, b, c], [a, c, e]]
    V.append(W); F = [np.array(F)]; base = len(W)
    # Close the two ends with fans.
    ends = np.array([[0, CY, ZS[0]], [0, CY, ZS[-1]]]); V.append(ends); fan = []
    for i in range(N):
        j = (i + 1) % N; fan += [[base, j, i], [base + 1, (n - 1) * N + i, (n - 1) * N + j]]
    F.append(np.array(fan)); base += 2
    def add(t):
        nonlocal base
        V.append(t[0]); F.append(t[1] + base); base += len(t[0])
    # Pan is a prism with a chamfered rear edge: polygon in (y, z), extruded along x.
    x0, x1, yb, z0, z1, ch = d['pan_x0'], d['pan_x1'], d['pan_y'], d['pan_z0'], d['pan_z1'], max(d['pan_ch'], 1.)
    prof = [(-110., z0), (yb, z0), (yb, z1 - ch), (yb + ch, z1), (-110., z1)]
    PV = np.array([[x, a_, b_] for x in (x0, x1) for a_, b_ in prof], float); m = len(prof); PF = []
    for i in range(m):
        j = (i + 1) % m; PF += [[i, j, m + j], [i, m + j, m + i]]
    for i in range(1, m - 1): PF += [[0, i, i + 1], [m, m + i + 1, m + i]]
    add((PV, np.array(PF)))
    fz = max(d['fd_z'], 62.)
    add(cyl_tris((d['tube_x'], d['fd_y'], fz), (30, d['fd_y'], fz), d['tube_r']))
    add(cyl_tris((30, d['fd_y'], fz), (d['boss_x'], d['fd_y'], fz), d['boss_r']))
    add(cyl_tris((d['tube_x'] - 16, d['fd_y'], fz), (d['tube_x'], d['fd_y'], fz), d['flange_r']))
    add(box_tris(-60, d['boss_x'], d['fd_y'], -110, fz - 40, fz + 40))
    add(cyl_tris((0, -89, ZS[-1] - 5), (0, -89, max(d['shaft_end'], 1000.)), 24.))
    for i in range(8):
        a_ = np.radians(d[f'b{i}_a']); zb = d[f'b{i}_z']; k_ = int(np.clip(np.searchsorted(ZS, zb) - 1, 0, n - 2)); w_ = (zb - ZS[k_]) / (ZS[k_ + 1] - ZS[k_])
        idx = int(round((a_ % (2 * np.pi)) / (2 * np.pi) * N)) % N; p0_ = ring[k_][idx, :2] - [0, CY]; p1_ = ring[k_ + 1][idx, :2] - [0, CY]; rr = float(np.hypot(*(p0_ * (1 - w_) + p1_ * w_)))
        tip = rr + d[f'b{i}_h']
        add(cyl_tris((0, CY, zb), (tip * np.cos(a_), CY + tip * np.sin(a_), zb), max(d[f'b{i}_r'], 2.)))
    return np.vstack(V), np.vstack(F)
class R:
    def __init__(s, F):
        arr = np.hstack([np.full((len(F), 1), 3, np.int64), F]).ravel().astype(ns.get_vtk_to_numpy_typemap()[vtk.VTK_ID_TYPE])
        s.ca = vtk.vtkCellArray(); s.ca.SetCells(len(F), ns.numpy_to_vtkIdTypeArray(arr, deep=True)); s.poly = vtk.vtkPolyData(); s.pts = vtk.vtkPoints(); s.poly.SetPoints(s.pts); s.poly.SetPolys(s.ca); s.w = {}
    def setv(s, V): s.pts.SetData(ns.numpy_to_vtk(V, deep=True)); s.pts.Modified(); s.poly.Modified()
    def win(s, W, H):
        if (W, H) not in s.w:
            m = vtk.vtkPolyDataMapper(); m.SetInputData(s.poly); m.ScalarVisibilityOff(); a = vtk.vtkActor(); a.SetMapper(m); a.GetProperty().SetColor(0, 0, 0); a.GetProperty().LightingOff()
            r = vtk.vtkRenderer(); r.SetBackground(1, 1, 1); r.AddActor(a); w = vtk.vtkRenderWindow(); w.SetOffScreenRendering(1); w.SetMultiSamples(0); w.SetSize(W, H); w.AddRenderer(r); w.Render()
            f = vtk.vtkWindowToImageFilter(); f.SetInput(w); f.SetInputBufferTypeToRGB(); f.ReadFrontBufferOff(); s.w[(W, H)] = (w, r, f)
        return s.w[(W, H)]
    def render(s, p, W, H, c):
        w, r, f = s.win(W, H); Rm = Rn.from_rotvec(p[:3]).as_matrix(); sc = np.exp(p[5]) * W; fp = np.exp(min(p[6], np.log(60.))) * W; u0, v0 = p[3] * W, p[4] * H
        t = np.array([(u0 - W / 2) / sc, (v0 - H / 2) / sc, fp / sc]); C = -Rm.T @ t + c
        cam = r.GetActiveCamera(); cam.SetPosition(*C); cam.SetFocalPoint(*(C + Rm.T @ np.array([0, 0, 1.]) * t[2])); cam.SetViewUp(*(-Rm.T @ np.array([0, 1., 0]))); cam.SetViewAngle(np.degrees(2 * np.arctan2(H / 2, fp))); cam.SetClippingRange(max(t[2] * .1, 1), t[2] * 5)
        w.Render(); f.Modified(); f.Update(); a = ns.vtk_to_numpy(f.GetOutput().GetPointData().GetScalars()).reshape(H, W, -1)[::-1, :, 0]
        return fill(a < 128)
def fill(m):
    h, w = m.shape; o = m.astype(np.uint8); ff = np.zeros((h + 2, w + 2), np.uint8); inv = o.copy(); cv2.floodFill(inv, ff, (0, 0), 2)
    for c in ((w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if inv[c[1], c[0]] == 0: cv2.floodFill(inv, ff, c, 2)
    return inv != 2
def iou(a, b):
    u = np.logical_or(a, b).sum(); return np.logical_and(a, b).sum() / u if u else 0.
def main():
    mdir, initf, out = sys.argv[1], sys.argv[2], sys.argv[3]; sweeps = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    L = 500; M = {}
    for n in TUNING:
        m = cv2.imread(f'{mdir}/{n}.png', 0); h, w = m.shape; k = L / max(h, w); W, H = int(round(w * k)), int(round(h * k)); M[n] = (cv2.resize(m, (W, H), interpolation=cv2.INTER_AREA) > 127, W, H)
    init = json.load(open(initf))
    x = np.r_[I0, O0, D0, [P0[k] for k in P0]]
    if 'x' in init: x = np.array(init['x'])
    if 'seed' in init:   # carry the previous fit forward, keep new parameters at their defaults
        s0 = init['seed']; x[:len(ZS)] = s0['ri']; x[len(ZS):2 * len(ZS)] = s0['ro']; x[2 * len(ZS):3 * len(ZS)] = s0['dxo']
        for i_, k in enumerate(P0):
            if k in s0['P'] and k not in ('tube_x', 'shaft_end', 'flange_r'): x[3 * len(ZS) + i_] = s0['P'][k]
        x[3 * len(ZS) + list(P0).index('tube_x')] = -205.; x[3 * len(ZS) + list(P0).index('shaft_end')] = 1085.; x[3 * len(ZS) + list(P0).index('flange_r')] = 60.
    x = clip_all(x)
    V0, F0 = build(x); ren = R(F0); ren.setv(V0)
    c = (V0.max(0) + V0.min(0)) / 2
    cams = {n: np.array(init['params'][n]) for n in TUNING}
    n_ = len(ZS)
    def score(xx):
        V, _ = build(xx); ren.setv(V); return float(np.average([iou(ren.render(cams[n], *M[n][1:], c), M[n][0]) for n in TUNING], weights=[WEIGHTS[n] for n in TUNING]))
    def smooth(xx):
        a, b, e = xx[:n_], xx[n_:2 * n_], xx[2 * n_:3 * n_]; return (np.abs(np.diff(a, 2)).sum() + np.abs(np.diff(b, 2)).sum() + np.abs(np.diff(e, 2)).sum()) * 2e-5
    def obj(xx): return 1 - score(xx) + smooth(xx)
    # Global camera search for the changed model first.
    def recam():
        V, _ = build(x); ren.setv(V)
        for n in TUNING:
            m, W, H = M[n]; best = (1e9, cams[n])
            starts = [cams[n]] + [np.r_[(Rn.from_rotvec(cams[n][:3]) * Rn.from_euler('xyz', d, degrees=True)).as_rotvec(), cams[n][3:]] for d in ([12, 0, 0], [-12, 0, 0], [0, 12, 0], [0, -12, 0], [0, 0, 12], [0, 0, -12])]
            for p0 in starts:
                r = minimize(lambda p, W=W, H=H, m=m: 1 - iou(ren.render(p, W, H, c), m), p0, method='Powell', options={'maxfev': 220, 'xtol': 1e-3, 'ftol': 1e-4, 'direc': np.diag([.05, .05, .05, .01, .01, .02, .2])})
                if r.fun < best[0]: best = (r.fun, r.x)
            cams[n] = best[1]
    if 'seed' not in init: pass
    t0 = time.time(); recam(); print('start', round(score(x), 4), round(time.time() - t0), flush=True)
    steps = np.r_[np.full(2 * n_, 5.), np.full(n_, 6.), [10, 10, 8, 15, 20, 8, 8, 5, 10, 3, 3, 10, 5, 2, 15, 6, 6, 15, 20], [10, 10, 6, 20, 25, 10, 10, 6, 20, 25, 8, 8, 5, 25, 25, 6, 6] + [10, 10, 4] * 5 + [15.] + [12., 20., 4., 6.] * 8]
    for sw in range(sweeps):
        for i in np.random.default_rng(sw).permutation(len(x)):
            best, bv = obj(x), x[i]
            for mul in (1, -1, .4, -.4):
                t = x.copy(); t[i] = bv + mul * steps[i]
                if (t[i] <= 0 and i < 2 * n_) or not within(t): continue
                v = obj(t)
                if v < best - 1e-6: best, x = v, t.copy()
        steps *= .75; recam(); s = score(x); print('sweep', sw, 'tuning IoU', round(s, 4), 'sec', round(time.time() - t0), flush=True)
        json.dump(dict(x=x.tolist(), params={n: cams[n].tolist() for n in TUNING}, ZS=ZS.tolist(), P=dict(zip(P0, x[3 * n_:].tolist())), ri=x[:n_].tolist(), ro=x[n_:2 * n_].tolist(), dxo=x[2 * n_:3 * n_].tolist(), center=c.tolist(), score=s), open(out, 'w'), indent=0)
    print('done', dict(zip(P0, np.round(x[3 * n_:], 1).tolist()))); print('ri', np.round(x[:n_], 0).tolist()); print('ro', np.round(x[n_:2 * n_], 0).tolist()); print('dxo', np.round(x[2 * n_:3 * n_], 0).tolist())
main()
