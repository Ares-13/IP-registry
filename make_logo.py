from PIL import Image

# Открываем твой логотип
img = Image.open("logo_pgups.png")
width, height = img.size

# Определяем самую длинную сторону
max_side = max(width, height)

# Создаем новый прозрачный квадратный фон
square_img = Image.new("RGBA", (max_side, max_side), (255, 255, 255, 0))

# Вычисляем координаты, чтобы вставить логотип ровно по центру
x = (max_side - width) // 2
y = (max_side - height) // 2

# Накладываем логотип на квадратный фон
square_img.paste(img, (x, y))

# Сохраняем правильную иконку
square_img.save("logo.ico", format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])