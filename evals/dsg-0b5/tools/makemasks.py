"""Object masks from reference images. usage: makemasks.py imgdir outdir"""
import sys, numpy as np, cv2
from scipy import ndimage as ndi
I, O = sys.argv[1], sys.argv[2]
def white(p, thr=236, sat=22):
    im = cv2.imread(p); h = im.shape[:2]
    g = im.astype(int); m = ~((g.min(2) >= thr) & (g.max(2) - g.min(2) <= sat))
    return im, m
def largest(m):
    l, n = ndi.label(m); 
    if n == 0: return m
    s = ndi.sum(m, l, range(1, n + 1)); return l == (1 + int(np.argmax(s)))
def clean(m, close=3, open_=2):
    m = ndi.binary_closing(m, iterations=close); m = ndi.binary_fill_holes(m); m = ndi.binary_opening(m, iterations=open_); return ndi.binary_fill_holes(largest(m))
S = {'user_photo': (f'{I}/user_photo.png', 225, 3, 4), 'p12': (f'{I}/ssp/p12_x501_1084x575.jpeg', 225, 2, 3), 'p27': (f'{I}/ssp/p27_x262_600x535.jpeg', 236, 3, 2), 'atm_transmission': (f'{I}/web/atm_transmission.jpg', 236, 3, 2)}
for n, (p, thr, cl, op) in S.items():
    im, m = white(p, thr, 30); m = clean(m, cl, op); cv2.imwrite(f'{O}/{n}.png', (m * 255).astype(np.uint8)); ov = im.copy(); e = m ^ ndi.binary_erosion(m, iterations=2); ov[e] = (0, 0, 255); cv2.imwrite(f'{O}/{n}_ov.png', ov); print(n, im.shape[:2], round(m.mean(), 3))
# Real photo on a light grey vignette with a soft blue shadow: seed from dark or textured pixels, then GrabCut.
p = f'{I}/web/tvs_dl501_conv.png'; im = cv2.imread(p); h, w = im.shape[:2]
g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(float); lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(g, (0, 0), 1.0), cv2.CV_64F)); tex = cv2.GaussianBlur(lap, (0, 0), 3.0)
hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV).astype(float)
fgseed = (g < 120) | (tex > 5.0)
bgseed = (tex < 0.8) & (g > 170)
mask = np.full((h, w), cv2.GC_PR_FGD, np.uint8); mask[bgseed] = cv2.GC_BGD; mask[fgseed] = cv2.GC_FGD
mask[:int(.15 * h)] = cv2.GC_BGD; mask[int(.86 * h):] = cv2.GC_BGD; mask[:, :int(.03 * w)] = cv2.GC_BGD; mask[:, int(.94 * w):] = cv2.GC_BGD
bg, fg = np.zeros((1, 65)), np.zeros((1, 65)); cv2.grabCut(im, mask, None, bg, fg, 6, cv2.GC_INIT_WITH_MASK)
m = (mask == 1) | (mask == 3); m = clean(m, 3, 3); cv2.imwrite(f'{O}/tvs_dl501.png', (m * 255).astype(np.uint8)); ov = im.copy(); e = m ^ ndi.binary_erosion(m, iterations=2); ov[e] = (0, 0, 255); cv2.imwrite(f'{O}/tvs_dl501_ov.png', ov); print('tvs', im.shape[:2], round(m.mean(), 3))
