"""Write an image-only PDF for the release smoke test.

No text layer on purpose: the only way redact-scan can find the witness name
is by actually running OCR, which is the code path the 0.15.0 binary shipped
without (pytesseract reached only via a dynamic import PyInstaller cannot see).
"""
from PIL import Image, ImageDraw, ImageFont

try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 64)
except OSError:
    font = ImageFont.load_default(size=64)

img = Image.new("RGB", (1700, 2200), "white")
draw = ImageDraw.Draw(img)
draw.text((150, 300), "NOTICE OF DEPOSITION", font=font, fill="black")
draw.text((150, 450), "WITNESS: MARGARET QUAGGA", font=font, fill="black")
img.save("smoke.pdf", "PDF", resolution=200.0)
print("wrote smoke.pdf")
