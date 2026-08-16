#!/usr/bin/env python3
"""测试 TextWriter 的 scale_x 是否会改变 PDF 中保存的字号"""
import fitz

# 创建测试PDF
doc = fitz.open()
page = doc.new_page(width=595, height=842)

# 字体文件
fontfile = "C:/Users/和稀泥的针尾鱼/Downloads/fangzhengxiaobiaosong_GBK/方正小标宋_GBK/方正小标宋_GBK.TTF"
fontsize = 35.79
text = "福建省人力资源和社会保障厅"

# 创建字体对象
font = fitz.Font(fontfile=fontfile)

# 测量文本宽度
text_width = font.text_length(text, fontsize=fontsize)
print(f"未压缩文本宽度: {text_width:.2f}pt")

# 计算横向压缩比例（压缩到原宽度的80%）
scale_x = 0.8
compressed_width = text_width * scale_x
print(f"压缩比例: {scale_x}")
print(f"压缩后宽度: {compressed_width:.2f}pt")

# 方案1：使用 TextWriter (不推荐，因为无法控制字间距)
tw = fitz.TextWriter(page.rect)
# TextWriter.append 参数：pos, text, font, fontsize, language, right_to_left, small_caps, color
# 注意：TextWriter 没有 scale_x 参数！需要用 morph
pos = fitz.Point(100, 200)
tw.append(pos, text, font=font, fontsize=fontsize)
# 应用到页面时使用 morph
tw.write_text(page, morph=(pos, fitz.Matrix(scale_x, 0, 0, 1.0, 0, 0)))

# 方案2：使用 insert_text + render_flags（测试是否有不同）
page.insert_text(
    (100, 300),
    text,
    fontsize=fontsize,
    fontfile=fontfile,
    fontname="TestFont",
    color=(1, 0, 0),
    morph=(fitz.Point(100, 300), fitz.Matrix(scale_x, 0, 0, 1.0, 0, 0))
)

# 方案3：不使用任何压缩，作为对比
page.insert_text(
    (100, 400),
    text,
    fontsize=fontsize,
    fontfile=fontfile,
    fontname="NormalFont",
    color=(0, 0, 1)
)

# 保存
output_path = "C:/Users/和稀泥的针尾鱼/Desktop/test_textwriter.pdf"
doc.save(output_path)
doc.close()

print(f"\n已保存到: {output_path}")
print("现在检查保存后的字号...")

# 重新打开检查
doc = fitz.open(output_path)
page = doc[0]
text_dict = page.get_text('dict')

print("\n=== 保存后的字号检查 ===")
for block in text_dict['blocks']:
    if block.get('type') == 0:
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                t = span.get('text', '')
                s = span.get('size', 0)
                f = span.get('font', '')
                if '福建' in t:
                    print(f"文字='{t}', 字号={s:.2f}pt, 字体={f}")

doc.close()
