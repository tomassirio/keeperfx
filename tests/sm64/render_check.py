# Render check for tools/sm64/make_mario_mod.py: a Mario-sized box, no ROM needed. Run: build/docker/sm64.sh
import math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tools/sm64"))
import make_mario_mod as m
from PIL import Image
# A box 80 wide, 160 tall, 40 deep standing on the origin, red front face (+z), blue elsewhere
def quad(a, b, c, d, n, col):
    uv = (1, 1)
    return [[(a, n, col, uv), (b, n, col, uv), (c, n, col, uv)], [(a, n, col, uv), (c, n, col, uv), (d, n, col, uv)]]
X, Y, Z = 40, 160, 20
tris = quad((-X,0,Z),(X,0,Z),(X,Y,Z),(-X,Y,Z),(0,0,1),(1,0,0)) + quad((-X,0,-Z),(X,0,-Z),(X,Y,-Z),(-X,Y,-Z),(0,0,-1),(0,0,1)) \
     + quad((X,0,-Z),(X,0,Z),(X,Y,Z),(X,Y,-Z),(1,0,0),(0,0,1)) + quad((-X,Y,-Z),(X,Y,-Z),(X,Y,Z),(-X,Y,Z),(0,1,0),(0,0,1))
tex = Image.new("RGBA", (m.TEX_W, m.TEX_H))
for d, want in ((0, "red"), (4, "blue")):
    img, feet = m.render(tris, tex, math.radians(45 * d), m.TD_PITCH, m.TD_PIXELS)
    box = img.getchannel("A").getbbox()
    r, g, b, a = img.getpixel((img.width // 2, (box[1] + box[3]) // 2))
    seen = "red" if r > b else "blue"
    print(f"dir {d}: size {img.size} feet row {feet} opaque box {box} centre colour {seen}")
    assert seen == want, "front face must face the camera at direction 0"
    assert box[3] < img.height, "nothing clipped at the bottom"
    assert abs(feet - box[3]) <= 8, "feet row near the bottom of the box (front edge tilts below it)"
    assert abs((box[0] + box[2]) / 2 - img.width / 2) <= 2, "must be horizontally centred"
    assert box[3] - box[1] >= m.TD_PIXELS * 0.8, "about TD_PIXELS tall"
print("render OK")
