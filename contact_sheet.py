from pathlib import Path
from PIL import Image, ImageDraw

directory = Path(__file__).parent / "previews" / "instagram"
files = sorted(directory.glob("*.png"))
cards = []
for path in files:
    image = Image.open(path).convert("RGB")
    image.thumbnail((270, 338))
    card = Image.new("RGB", (290, 378), "#222222")
    card.paste(image, ((290 - image.width) // 2, 8))
    ImageDraw.Draw(card).text((10, 350), path.name, fill="white")
    cards.append(card)
sheet = Image.new("RGB", (1160, 378 * ((len(cards) + 3) // 4)), "#111111")
for index, card in enumerate(cards):
    sheet.paste(card, ((index % 4) * 290, (index // 4) * 378))
sheet.save(directory / "contact_sheet.jpg", quality=90)
