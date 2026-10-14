#!/usr/bin/env python3
"""Build original geometric character art. Pillow is a development dependency."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'assets'


def character(pose):
    im = Image.new('RGBA', (600, 800))
    d = ImageDraw.Draw(im)
    # A warm, restrained illustrated butler; transparent, with no external art.
    d.ellipse((100, 744, 506, 788), fill=(0, 0, 0, 30))
    d.rounded_rectangle((111, 385, 489, 786), 100, fill='#172937')
    d.polygon([(145, 451), (82, 651), (113, 712), (194, 519)], fill='#233d4b')
    d.polygon([(455, 451), (518, 651), (486, 712), (406, 519)], fill='#233d4b')
    d.ellipse((89, 662, 151, 724), fill='#d3b599')
    d.ellipse((448, 662, 510, 724), fill='#d3b599')
    d.rounded_rectangle((242, 328, 358, 458), 30, fill='#cfae90')
    d.polygon([(224, 400), (300, 447), (375, 400), (335, 687), (265, 687)], fill='#e7e9df')
    d.polygon([(215, 395), (281, 449), (247, 479), (286, 712), (158, 485)], fill='#2f4854')
    d.polygon([(385, 395), (319, 449), (353, 479), (314, 712), (442, 485)], fill='#263e4c')
    d.polygon([(246, 433), (286, 447), (286, 466), (243, 480)], fill='#b99762')
    d.polygon([(354, 433), (314, 447), (314, 466), (357, 480)], fill='#b99762')
    d.rounded_rectangle((285, 444, 315, 468), 7, fill='#e0c28f')
    d.line((300, 510, 300, 750), fill='#60717a', width=2)
    for y in [546, 592, 638]: d.ellipse((295, y, 305, y+10), fill='#bca471')
    d.rounded_rectangle((373, 531, 427, 538), 3, fill='#9eafad')
    d.polygon([(384, 530), (395, 513), (406, 530)], fill='#e6e9dd')
    # Ears, face and silver hair.
    d.ellipse((146, 188, 206, 280), fill='#caa98b')
    d.ellipse((394, 188, 454, 280), fill='#caa98b')
    d.rounded_rectangle((169, 85, 431, 371), 116, fill='#ddbea0')
    d.pieslice((161, 53, 439, 269), 180, 355, fill='#b8c1bd')
    d.polygon([(174, 145), (193, 108), (245, 88), (296, 96), (244, 145), (193, 168), (182, 232), (170, 225)], fill='#d9ded5')
    d.polygon([(426, 142), (408, 109), (362, 91), (324, 110), (377, 149), (407, 171), (417, 232), (431, 216)], fill='#9ba9a8')
    # Brows and eyes change for listening, thought and failure.
    brow = -10 if pose=='listening' else (6 if pose=='failed' else 0)
    d.line((213, 196+brow, 261, 191+brow), fill='#677b7d', width=9)
    d.line((338, 191, 386, 197), fill='#677b7d', width=9)
    for x in [237, 362]:
        d.ellipse((x-12, 216, x+12, 234), fill='#f2eee1')
        shift=5 if pose=='thinking' else 0
        d.ellipse((x-5+shift, 216, x+5+shift, 233), fill='#203940')
    d.arc((281, 222, 315, 286), 270, 80, fill='#ad8a6e', width=4)
    # Silver moustache and visible mouth animation.
    d.polygon([(299, 282), (277, 279), (246, 298), (274, 302), (299, 292)], fill='#d7ded8')
    d.polygon([(301, 282), (323, 279), (354, 298), (326, 302), (301, 292)], fill='#becbc8')
    if pose=='speaking-1': d.ellipse((280, 306, 320, 333), fill='#63463f')
    elif pose=='speaking-0': d.ellipse((280, 311, 320, 322), fill='#63463f')
    elif pose=='failed': d.arc((275, 313, 325, 340), 205, 335, fill='#8d6758', width=4)
    else: d.arc((273, 296, 327, 328), 10, 170, fill='#906957', width=4)
    if pose=='listening':
        d.arc((447, 171, 485, 260), 285, 75, fill='#a7d7c9', width=5)
        d.arc((451, 153, 507, 278), 285, 75, fill='#7caaa2', width=4)
    return im


if __name__ == '__main__':
    DEST.mkdir(exist_ok=True)
    for pose in ['idle', 'listening', 'thinking', 'speaking-0', 'speaking-1', 'failed']:
        character(pose).save(DEST / f'butler-{pose}.png', optimize=True)
    Image.new('RGBA', (8, 8), (14, 27, 36, 239)).save(DEST / 'panel.png')
