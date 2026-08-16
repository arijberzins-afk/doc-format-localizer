---
name: doc-format-localizer
description: >
  对政府公文图片或 PDF 进行"格式保留本地化"——把文件中的省份、机关等地域信息替换为目标省份对应信息，
  同时完全保留原始版面、字体、颜色和排版。
  触发词：「格式保留替换」「直接在PDF/图片上改」「不用重排」「保留原格式」「只改省份/机关名」「改成XX省版本」等。
---

# 格式保留本地化

将外省政府公文（PDF/图片）的省份、机关等地域信息原位替换为目标省份信息，不重排版面。

## 从用户输入提取参数

用户会用自然语言说明目标省份，例如：

- "帮我把这个改成浙江省版本"
- "改成山东省的"
- "换成广西壮族自治区"

从中提取 `--target-province` 的值：
- "浙江省" / "山东省" / "广西壮族自治区" 等（保留完整省份名，含"省"/"市"/"自治区"后缀）
- 目标机关名**无需用户提供**，脚本自动从原文发文机关推导

## 调用方式

```bash
python scripts/localize_preserve_format.py \
  --input 输入文件.pdf \
  --target-province "目标省份名" \
  --config config.json \
  --no-preview
```

多文件批量处理：每个文件各加一个 `--input`。

可选参数：
- `--target-authority`：目标机关全称（不填则自动推导）
- `--output-dir`：输出目录（默认桌面）
- `--no-preview`：跳过生成预览 PNG
- `--font-dir`：额外字体目录

## 输出

- 文件命名：`原文件名_localized.扩展名`，输出到桌面
- PDF 额外输出每页 PNG 预览

## 配置文件 config.json

```json
{
  "qwen_api_key": "API Key",
  "qwen_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "llm_disable_proxy": false
}
```

## 替换逻辑

LLM 只输出**省市地名本身**，不带机构职能后缀，确保替换前后字数一致、版面不变。
正则兜底自动转换发文字号前缀（如 `粤人社规〔2022〕22号` → 目标省简称）。

## 依赖

```
pymupdf pillow numpy requests
```
