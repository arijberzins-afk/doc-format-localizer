---
name: doc-format-localizer
description: >
  对政府公文图片或 PDF 进行"格式保留本地化"——把文件中的省份、机关等地域信息替换为福建省对应信息，
  同时完全保留原始版面、字体、颜色和排版，不走 DOCX 重排流程。
  图片输入：Qwen-VL 识别各文字块的字体/位置/颜色 → PIL 像素级覆盖写入新文字，原排版不变。
  PDF 输入：PyMuPDF 从 span 精确读取字号/颜色/基线坐标 → redact 擦除原词 → 系统字体写回新词，原版面不变。
  触发词：「格式保留替换」「直接在图片/PDF上改」「不用重排」「保留原格式」「只改省份/机关名」
  「改成福建省版本但不重排」「像素级替换」「redact 替换」等。
  只要用户强调要保留原始排版、不走 DOCX 流程，就必须优先使用本技能而非 gov-doc-generator。
---

# 格式保留本地化

本技能将图片或 PDF 公文中的省份/机关等短词**原位替换**，不重排版面。

## 使用方式

```powershell
python scripts/localize_preserve_format.py `
  --input 输入文件.jpg `
  --target-authority "福建省人力资源和社会保障厅" `
  --target-province "福建省" `
  --config C:/Users/.../gov-doc-generator/config.json
```

多个图片或 PDF 分别指定 `--input`，每个文件独立输出。

可选参数：
- `--output-dir`：输出目录，默认与输入文件同目录
- `--no-preview`：跳过生成预览 PNG（仅对 PDF 输出有效）
- `--font-dir`：指定额外字体目录（优先于系统字体）

## 工作流程

### 图片输入（.jpg/.jpeg/.png/.bmp/.tif）

1. **Qwen-VL 字体布局识别**：调用 `qwen-vl-max`，返回每个文字块的 `text`、`y_pct`（顶部纵向百分比）、`font`、`size_hint`、`bold`、`color`
2. **位置兜底**：Qwen-VL 未能识别字体时，按 `y_pct` 和 `bold` 推断：顶部红字→方正小标宋、加粗→黑体、其余→仿宋_GB2312
3. **替换词表生成**：只替换短词（≤25字符）——机关名和省份名。不替换整段正文，避免覆盖破坏多行排版
4. **PIL 覆盖写入**：用白色矩形覆盖原文字区域，用同款字体+同色在原坐标写入新文字

### PDF 输入（.pdf）

1. **LLM 生成精简替换词表**：提取 PDF 文字层全文，调 LLM 直接输出 `{原词: 新词}` JSON，只替换省份名、机关名、发文字号前缀三类短词（原词≤25字符、不含换行、在原文中可直接搜索到）。
2. **从 span 精确读取样式**：用 `page.get_text("dict")` 遍历所有 span，对每个替换词找到包含它的 span，读取：
   - `span["size"]` → 字号（**不得用 rect.height 估算**，行高≠字号）
   - `span["color"]` → 颜色 int，转为 0-1 RGB tuple
   - `span["font"]` → 字体名，用于系统字体回退映射
   - `span["origin"][1]` → 基线 y 坐标（**不得用 rect.y1 作为插入 y**，insert_text 需要基线而非矩形底部）
3. **redact 覆盖 + 系统字体写回**：先对所有目标矩形批量 `add_redact_annot` + `apply_redactions` 白色覆盖，再用 `insert_text(point=(rect.x0, origin_y), fontsize=span_size, color=color_rgb, fontname=font_key, fontfile=system_font)` 写入新词。
   - `rect.x0`：来自 `search_for`，是词的精确起始 x
   - `origin_y`：来自 `span["origin"][1]`，是该行的文字基线
4. **嵌入字体不可复用**：PDF 嵌入字体是 CID 子集（无 cmap 表，字形名为匿名 glyph ID），提取出来无法用于新词写入，一律用系统字体回退。
5. **生成预览 PNG**：保存每页为 PNG 便于检查。

## 字体映射规则

```
FangSong / 仿宋_GB2312  →  C:/Windows/Fonts/simfang.ttf
SimHei / 黑体           →  C:/Windows/Fonts/simhei.ttf
KaiTi / 楷体            →  C:/Windows/Fonts/simkai.ttf
SimSun / 宋体 / FZXBSJW →  C:/Windows/Fonts/simsun.ttc
其余未知字体            →  C:/Windows/Fonts/simfang.ttf（兜底）
```

嵌入字体（CID子集）不用于写回，统一走系统字体。

## 典型替换规则

以广东→福建为例（可通过参数自定义）：

| 原词 | 新词 |
|------|------|
| 广东省人力资源和社会保障厅 | 福建省人力资源和社会保障厅 |
| 广东省 | 福建省 |
| 粤 | 闽（仅发文字号前缀） |
| 广人社 | 闽人社 |

LLM 负责判断替换范围，避免误改正文叙述中的地名（如"与广东省签订协议"不应改动）。

## 脚本位置

- `scripts/localize_preserve_format.py`：主脚本，图片/PDF 两路均在此实现

## 依赖

```
Pillow>=9.0
pymupdf>=1.23   # fitz
requests
paddleocr>=2.7  # 图片流程字级定位（可选，未安装时回退到行级替换）
paddlepaddle    # PaddleOCR 后端
```

配置文件复用 gov-doc-generator 的 `config.json`（读取 `qwen_api_key`、`qwen_endpoint`、`llm_disable_proxy`）。
