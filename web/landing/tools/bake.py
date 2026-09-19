"""bake.py — put a trained splat into the LANDING FRAME, once and for all.

    python3 tools/bake.py <in.ply> <out.ply>

splat.js has a `canonical` fast path: when the manifest says canonical:true it does a
PURE UNIFORM SCALE — no percentile bounds, no re-fit — so feet stay exactly on y=0 and
the mast exactly on x=z=0. That path is only correct if the file is already:

    +Y up, upright        the mast standing, not leaning
    feet on y = 0         min-y of the cloud is zero
    centred x = z = 0     on the mast axis
    exactly 1.0 tall      so `height` in the manifest is a plain multiplier

This writes that file. The upright rotation is derived from the geometry, not guessed:
PCA on the gaussian centres gives the mast as the dominant axis, and the WIDER end of
that axis is the wheel base, which resolves the sign.

CRITICAL: a gaussian carries an orientation quaternion as well as a position. Rotating
only the positions leaves every anisotropic blob pointing the old way, which reads as
smearing rather than as a rotation. Both are rotated here.
"""
import re, struct, sys
import numpy as np

def read_ply(p):
    f = open(p, 'rb'); hdr = b''
    while b'end_header' not in hdr: hdr += f.readline()
    txt = hdr.decode('ascii', 'replace')
    n = int(re.search(r'element vertex (\d+)', txt).group(1))
    props = re.findall(r'property (\w+) (\w+)', txt)
    fmt = {'float': 'f', 'double': 'd', 'uchar': 'B', 'int': 'i', 'uint': 'I'}
    dt = np.dtype([(nm, fmt[t]) for t, nm in props])
    return hdr, np.frombuffer(f.read(n * dt.itemsize), dtype=dt, count=n).copy()

def mat_to_quat(M):
    tr = M.trace()
    if tr > 0:
        S = np.sqrt(tr + 1) * 2
        return np.array([0.25*S, (M[2,1]-M[1,2])/S, (M[0,2]-M[2,0])/S, (M[1,0]-M[0,1])/S])
    i = int(np.argmax([M[0,0], M[1,1], M[2,2]]))
    if i == 0:
        S = np.sqrt(1 + M[0,0] - M[1,1] - M[2,2]) * 2
        return np.array([(M[2,1]-M[1,2])/S, 0.25*S, (M[0,1]+M[1,0])/S, (M[0,2]+M[2,0])/S])
    if i == 1:
        S = np.sqrt(1 + M[1,1] - M[0,0] - M[2,2]) * 2
        return np.array([(M[0,2]-M[2,0])/S, (M[0,1]+M[1,0])/S, 0.25*S, (M[1,2]+M[2,1])/S])
    S = np.sqrt(1 + M[2,2] - M[0,0] - M[1,1]) * 2
    return np.array([(M[1,0]-M[0,1])/S, (M[0,2]+M[2,0])/S, (M[1,2]+M[2,1])/S, 0.25*S])

def rot_to(a, t):
    a = a / np.linalg.norm(a); v = np.cross(a, t); s = np.linalg.norm(v); c = a @ t
    if s < 1e-9: return np.eye(3) if c > 0 else -np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K * ((1 - c) / s**2)

src, dst = sys.argv[1], sys.argv[2]
hdr, d = read_ply(src)
xyz = np.stack([d['x'], d['y'], d['z']], 1).astype(np.float64)

X = xyz - xyz.mean(0)
_, sv, vt = np.linalg.svd(X, full_matrices=False)
axis = vt[0]
t = X @ axis
lo = X[t < np.percentile(t, 15)]; hi = X[t > np.percentile(t, 85)]
spread = lambda P: np.linalg.norm(P - P.mean(0), axis=1).mean()
if spread(lo) < spread(hi): axis = -axis        # the WIDER end is the wheel base
R = rot_to(axis, np.array([0., 1., 0.]))
print(f"  axis lengths {np.round(sv/np.sqrt(len(xyz)),3)}  mast {np.round(axis,4)}")

xyz = xyz @ R.T
xyz[:, 0] -= np.median(xyz[:, 0]); xyz[:, 2] -= np.median(xyz[:, 2])
xyz[:, 1] -= xyz[:, 1].min()
s = 1.0 / (xyz[:, 1].max() - xyz[:, 1].min())   # exactly 1.0 tall
xyz *= s
d['x'], d['y'], d['z'] = xyz[:, 0], xyz[:, 1], xyz[:, 2]

q = np.stack([d['rot_0'], d['rot_1'], d['rot_2'], d['rot_3']], 1).astype(np.float64)
w1, x1, y1, z1 = mat_to_quat(R); w2, x2, y2, z2 = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
out = np.stack([w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
                w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2], 1)
out /= np.linalg.norm(out, axis=1, keepdims=True)
for i in range(4): d[f'rot_{i}'] = out[:, i]

for k in ('scale_0', 'scale_1', 'scale_2'):     # scales are stored as log
    if k in d.dtype.names: d[k] = d[k] + np.log(s)

open(dst, 'wb').write(hdr + d.tobytes())
e = np.stack([d['x'], d['y'], d['z']], 1)
print(f"  wrote {dst}")
print(f"  bounds {np.ptp(e[:,0]):.3f} x {np.ptp(e[:,1]):.3f} x {np.ptp(e[:,2]):.3f}"
      f"   feet y={e[:,1].min():.4f}  centre x={np.median(e[:,0]):.4f} z={np.median(e[:,2]):.4f}")
