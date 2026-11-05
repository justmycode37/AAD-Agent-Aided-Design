import sys, numpy as np, cv2
from scipy import ndimage as ndi
I, O = sys.argv[1], sys.argv[2]; im = cv2.imread(f'{I}/ssp/p20_x503_636x757.jpeg'); g = im.astype(int)
m = ~((g.min(2) >= 236) & (g.max(2) - g.min(2) <= 30))
hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV); green = (hsv[..., 0] > 40) & (hsv[..., 0] < 95) & (hsv[..., 1] > 55)
crop = np.zeros_like(m); crop[165:482, 196:636] = True
m = m & crop
m[ndi.binary_dilation(green, iterations=1)] = False
m = ndi.binary_opening(m, iterations=2); l, n = ndi.label(m); s = ndi.sum(m, l, range(1, n + 1)); m = l == (1 + int(np.argmax(s)))
patch = np.zeros_like(m); patch[295:368, 250:348] = True; nw = ~((g.min(2) >= 236) & (g.max(2) - g.min(2) <= 30)); m = m | (patch & nw)
m = ndi.binary_closing(m, iterations=4); m = ndi.binary_fill_holes(m); l, n = ndi.label(m); s = ndi.sum(m, l, range(1, n + 1)); m = l == (1 + int(np.argmax(s))); cv2.imwrite(f'{O}/p20.png', (m * 255).astype(np.uint8)); ov = im.copy(); ov[m ^ ndi.binary_erosion(m, iterations=1)] = (0, 0, 255)
cv2.imwrite(f"{O}/p20_ov.png", cv2.resize(ov[150:500, 180:636], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)); print(round(m.mean(), 3))
