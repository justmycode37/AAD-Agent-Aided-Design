"""Silhouette comparison against reference pictures. See PREREG.md Amendment 1.
usage: imgfit.py model.npz masksdir imgroot outdir [--images a,b] [--seed 3] [--init prev.json]
A rigid camera (rotation, translation, focal length) is fitted per image. The model itself never changes."""
import argparse, json, os, time
import numpy as np, cv2, vtk
from vtk.util import numpy_support as ns
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation as Rn
ORIG = {'p20': 'ssp/p20_x503_636x757.jpeg', 'user_photo': 'user_photo.png', 'p12': 'ssp/p12_x501_1084x575.jpeg', 'p27': 'ssp/p27_x262_600x535.jpeg', 'atm_transmission': 'web/atm_transmission.jpg', 'tvs_dl501': 'web/tvs_dl501_conv.png'}
TUNING = ('user_photo', 'p12', 'p27', 'p20'); HOLDOUT = ('atm_transmission', 'tvs_dl501')

class Sil:
    def __init__(s, v, f):
        pts = vtk.vtkPoints(); pts.SetData(ns.numpy_to_vtk(v, deep=True))
        arr = np.hstack([np.full((len(f), 1), 3, np.int64), f]).ravel().astype(ns.get_vtk_to_numpy_typemap()[vtk.VTK_ID_TYPE])
        ca = vtk.vtkCellArray(); ca.SetCells(len(f), ns.numpy_to_vtkIdTypeArray(arr, deep=True))
        s.poly = vtk.vtkPolyData(); s.poly.SetPoints(pts); s.poly.SetPolys(ca); s.win = {}
    def _win(s, W, H):
        k = (W, H)
        if k not in s.win:
            m = vtk.vtkPolyDataMapper(); m.SetInputData(s.poly); m.ScalarVisibilityOff()
            a = vtk.vtkActor(); a.SetMapper(m); a.GetProperty().SetColor(0, 0, 0); a.GetProperty().LightingOff(); a.GetProperty().BackfaceCullingOff()
            r = vtk.vtkRenderer(); r.SetBackground(1, 1, 1); r.AddActor(a)
            w = vtk.vtkRenderWindow(); w.SetOffScreenRendering(1); w.SetMultiSamples(0); w.SetSize(W, H); w.AddRenderer(r); w.Render()
            f = vtk.vtkWindowToImageFilter(); f.SetInput(w); f.SetInputBufferTypeToRGB(); f.ReadFrontBufferOff()
            s.win[k] = (w, r, f, m, a)
        return s.win[k]
    def render(s, p, W, H):
        """p: rvec(3), u0, v0 in fractions of W/H, log px-per-mm at unit W, log focal/W."""
        w, r, f, *_ = s._win(W, H); R = Rn.from_rotvec(p[:3]).as_matrix()
        sc = np.exp(p[5]) * W; fpx = np.exp(min(p[6], np.log(60.))) * W; u0, v0 = p[3] * W, p[4] * H
        t = np.array([(u0 - W / 2) / sc, (v0 - H / 2) / sc, fpx / sc]); C = -R.T @ t
        cam = r.GetActiveCamera(); cam.SetPosition(*C); cam.SetFocalPoint(*(C + R.T @ np.array([0, 0, 1.]) * t[2])); cam.SetViewUp(*(-R.T @ np.array([0, 1., 0])))
        cam.SetViewAngle(np.degrees(2 * np.arctan2(H / 2, fpx))); cam.SetClippingRange(max(t[2] * .1, 1), t[2] * 5)
        w.Render(); f.Modified(); f.Update(); im = f.GetOutput()
        a = ns.vtk_to_numpy(im.GetPointData().GetScalars()).reshape(H, W, -1)[::-1, :, 0]
        return fill(a < 128)

def fill(m):
    h, w = m.shape; o = (m.astype(np.uint8)); ff = np.zeros((h + 2, w + 2), np.uint8); inv = o.copy(); cv2.floodFill(inv, ff, (0, 0), 2)
    for c in ((w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if inv[c[1], c[0]] == 0: cv2.floodFill(inv, ff, c, 2)
    return inv != 2

def iou(a, b):
    u = np.logical_or(a, b).sum(); return np.logical_and(a, b).sum() / u if u else 0.

def contour_f(a, b, tau):
    def edge(m): return (m.astype(np.uint8) - cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8))) > 0
    ea, eb = edge(a), edge(b)
    if not ea.any() or not eb.any(): return 0., 0., 0.
    da = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 5); db = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 5)
    p, r = float((db[ea] <= tau).mean()), float((da[eb] <= tau).mean()); return (2 * p * r / (p + r) if p + r else 0.), p, r

def resize_mask(m, L):
    h, w = m.shape; k = L / max(h, w); W, H = int(round(w * k)), int(round(h * k))
    return (cv2.resize(m.astype(np.uint8) * 255, (W, H), interpolation=cv2.INTER_AREA) > 127), W, H

def fit(S, name, mask, V, seed, init=None):
    rng = np.random.default_rng(seed); ext = V.max(0) - V.min(0); D = np.linalg.norm(ext)
    low, W0, H0 = resize_mask(mask, 200); ys, xs = np.where(low); bw = max(xs.max() - xs.min(), ys.max() - ys.min()); u0, v0 = xs.mean() / W0, ys.mean() / H0
    s0 = np.log(bw / (D * .9) / W0)
    def f(p, m, W, H): return 1 - iou(S.render(p, W, H), m)
    cands = []
    if init is None:
        qs = Rn.random(1400, random_state=seed)
        pf = np.log(4.0)
        for q in qs:
            p = np.r_[q.as_rotvec(), u0, v0, s0, pf]; cands.append((f(p, low, W0, H0), p))
        cands.sort(key=lambda c: c[0])
        tops = []
        for e, p in cands[:120]:   # keep diverse starting rotations
            if all((Rn.from_rotvec(p[:3]) * Rn.from_rotvec(q[:3]).inv()).magnitude() > .35 for _, q in tops): tops.append((e, p))
            if len(tops) == 8: break
    else:
        tops = [(0, np.array(init))]
    m1, W1, H1 = resize_mask(mask, 400); best = None
    for _, p in tops:
        r = minimize(f, p, args=(m1, W1, H1), method='Powell', options={'maxfev': 500, 'xtol': 1e-3, 'ftol': 1e-4, 'direc': np.diag([.15, .15, .15, .03, .03, .05, .3])})
        if best is None or r.fun < best.fun: best = r
    m2, W2, H2 = resize_mask(mask, 1000)
    r = minimize(f, best.x, args=(m2, W2, H2), method='Powell', options={'maxfev': 500, 'xtol': 5e-4, 'ftol': 2e-5, 'direc': np.diag([.03, .03, .03, .005, .005, .01, .3])})
    pred = S.render(r.x, W2, H2); I = iou(pred, m2); dg = np.hypot(*(np.array(np.where(m2)).max(1) - np.array(np.where(m2)).min(1)))
    tau = .0025 * dg; cf, cp, cr = contour_f(pred, m2, tau)
    return r.x, pred, m2, W2, H2, dict(name=name, IoU=round(float(I), 4), contour_F=round(cf, 4), contour_p=round(cp, 4), contour_r=round(cr, 4), tau_px=round(float(tau), 2), px_per_mm=round(float(np.exp(r.x[5]) * W2), 4), focal_over_W=round(float(np.exp(min(r.x[6], np.log(60.)))), 2), model_only=round(float((pred & ~m2).sum() / m2.sum()), 4), ref_only=round(float((m2 & ~pred).sum() / m2.sum()), 4))

def main():
    a = argparse.ArgumentParser(); a.add_argument('model'); a.add_argument('masks'); a.add_argument('root'); a.add_argument('out'); a.add_argument('--images'); a.add_argument('--seed', type=int, default=3); a.add_argument('--init')
    A = a.parse_args(); os.makedirs(A.out, exist_ok=True); z = np.load(A.model); v, f = z['v'].astype(float), z['f'].astype(np.int64)
    c = (v.max(0) + v.min(0)) / 2; V = v - c; S = Sil(V, f); res = {}; names = A.images.split(',') if A.images else list(TUNING + HOLDOUT)
    init = json.load(open(A.init))['params'] if A.init else {}
    params = {}
    for n in names:
        t = time.time(); m = cv2.imread(f'{A.masks}/{n}.png', 0) > 127; p, pred, m2, W, H, r = fit(S, n, m, V, A.seed, init.get(n)); r['seconds'] = round(time.time() - t); params[n] = p.tolist(); res[n] = r
        im = cv2.resize(cv2.imread(f'{A.root}/{ORIG[n]}'), (W, H), interpolation=cv2.INTER_AREA); ov = im.copy()
        ov[pred & ~m2] = (ov[pred & ~m2] * .4 + np.array([0, 0, 255]) * .6).astype(np.uint8); ov[m2 & ~pred] = (ov[m2 & ~pred] * .4 + np.array([255, 120, 0]) * .6).astype(np.uint8)
        cs, _ = cv2.findContours(pred.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE); cv2.drawContours(ov, cs, -1, (0, 200, 0), 1); cv2.imwrite(f'{A.out}/{n}_overlay.png', ov)
        print(json.dumps(r), flush=True)
    def mean(k, ns_): vals = [res[x][k] for x in ns_ if x in res]; return round(float(np.mean(vals)), 4) if vals else None
    out = dict(images=res, params=params, tuning_mean_IoU=mean('IoU', TUNING), holdout_mean_IoU=mean('IoU', HOLDOUT), tuning_mean_contour_F=mean('contour_F', TUNING), holdout_mean_contour_F=mean('contour_F', HOLDOUT))
    out['IMG99_PASS'] = bool(out['tuning_mean_IoU'] and out['holdout_mean_IoU'] and out['tuning_mean_IoU'] >= .99 and out['holdout_mean_IoU'] >= .99)
    json.dump(out, open(f'{A.out}/result.json', 'w'), indent=1); print({k: out[k] for k in out if k not in ('images', 'params')})
main()
