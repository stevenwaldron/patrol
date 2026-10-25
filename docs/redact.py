"""Find sensitive text in a screenshot with OCR and cover it with a solid box."""
import sys, re, subprocess, csv, io
from PIL import Image, ImageDraw

PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),   # email addresses
    re.compile(r"@gmail", re.I),
    re.compile(r"^AID[A-Z0-9]{10,}"),          # IAM user unique IDs
    re.compile(r"^ARO[A-Z0-9]{10,}"),          # IAM role unique IDs
    re.compile(r"hooks\.slack\.com", re.I),    # Slack webhook URLs
    re.compile(r"^/?services/T[A-Z0-9]{6,}"),  # webhook path if OCR splits the URL
    re.compile(r"^T[A-Z0-9]{8,}/B[A-Z0-9]{8,}"),
]

def redact(src, dst):
    img = Image.open(src).convert("RGB")
    big = img.resize((img.width * 2, img.height * 2))  # upscale helps OCR on terminal text
    tsv = subprocess.run(["tesseract", "-", "-", "--psm", "11", "tsv"],
                         input=_png(big), capture_output=True).stdout.decode()
    draw, hits = ImageDraw.Draw(img), []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        word = (row.get("text") or "").strip().strip('",')
        if word and any(p.search(word) for p in PATTERNS):
            x, y, w, h = (int(row[k]) // 2 for k in ("left", "top", "width", "height"))
            pad = 4
            draw.rectangle([x - pad, y - pad, x + w + pad, y + h + pad], fill=(0, 0, 0))
            hits.append(word)
    img.save(dst)
    return hits

def _png(im):
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()

if __name__ == "__main__":
    print(sys.argv[1].split("/")[-1], "->", redact(sys.argv[1], sys.argv[2]))
