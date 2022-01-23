#!/usr/bin/python3
"""This script creates the images with the vertical and horizontal beams"""

# import section
from PIL import Image, ImageDraw

# the given angles - MUST BE 8
angles = [-26.6, -19.0, -11.4, -3.8, +3.8, +11.4, 19.0, 26.6]

# horizontal and vertical beam widths
horzbw = 15
vertbw = 20

# output path
path = "scripts/web/static/img/azel"

# anti-aliasing factor
aa = 4

# azimuth image dimensions
azwidth = 12
azheight = 12

# azimuth image with transparency and no background
aawidth = azwidth * aa
aaheight = azheight * aa
for i in range(64):
  img = Image.new('RGBA', (aawidth, aaheight), (0, 0, 0, 0))
  draw = ImageDraw.Draw(img)
  draw.pieslice(xy=[(0, 0), (aawidth, 2*aaheight)], start=270 - 90, end=270 + 90, fill=(200, 200, 200), width=1)
  draw.pieslice(xy=[(0, 0), (aawidth, 2*aaheight)], start=270 + angles[i % 8] - horzbw/2, end=270 + angles[i % 8] + horzbw/2, fill=(55, 0, 200), width=1)
  img = img.resize([azwidth, azheight], resample=Image.ANTIALIAS)
  img.save(path + "/az" + str(i) + '.png')

# elevation image dimensions
elwidth = 12
elheight = 12

# azimuth image with transparency and no background
aawidth = elwidth * aa
aaheight = elheight * aa
for i in range(64):
  img = Image.new('RGBA', (aawidth, aaheight), (0, 0, 0, 0))
  draw = ImageDraw.Draw(img)
  draw.pieslice(xy=[(0, 0), (2*aawidth, aaheight)], start=180 - 90, end=180 + 90, fill=(200, 200, 200), width=1)
  draw.pieslice(xy=[(0, 0), (2*aawidth, aaheight)], start=180 + angles[i % 8] - horzbw/2, end=180 + angles[i % 8] + horzbw/2, fill=(200, 0, 55), width=1)
  img = img.resize([elwidth, elheight], resample=Image.ANTIALIAS)
  img.save(path + "/el" + str(i) + '.png')
