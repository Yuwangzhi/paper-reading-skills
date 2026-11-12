# 输入格式与可复用命令

使用 Python 3.9+。`scripts/requirements.txt` 仅安装 BeautifulSoup；PDF 提取另装 PyMuPDF。脚本不翻译、不生成批注、不连接模型。所有相对图片路径相对于 `source.json` 所在目录，图像必须位于该目录之内；远程图像先按实际来源保存到此目录。完整例子见 [source](../assets/example-source.json) 和 [annotations](../assets/example-annotations.json)。

## 1. 提取初稿

```bash
python <skill目录>/scripts/extract_source.py --input paper.html --output source.json --url https://arxiv.org/html/XXXX.XXXXXv2 --version v2
python <skill目录>/scripts/extract_source.py --input paper.pdf --output source.json --version v2
```

HTML 默认选 `article`，不存在则取 body；可用 `--content-selector` 缩小正文、`--block-selector` 明确块选择器。默认识别 LaTeXML/常规段落、图、表、公式、参考文献、案例框；复杂网页要人工修复清单。PDF 按页面/文本块建立初稿，**不能自动恢复跨栏阅读顺序或图表语义**。图片、公式和附录须另行恢复，初稿均为 `excerpt + draft`，不承诺全文。

## 2. source.json

```json
{
  "schema_version": 1,
  "mode": "full",
  "title": "Paper title",
  "title_zh": "论文中文名",
  "course": "论文精读 / 研究讨论",
  "source": {"url": "https://example.org/paper-v2", "version": "v2", "sha256": "输入源文件的 SHA-256"},
  "review": {"status": "verified", "basis": "对照所给 v2 PDF 的正文、图表、公式、参考文献与全部附录逐项核对"},
  "sections": [{"id": "intro", "title": "Introduction / 引言", "blocks": [{
    "id": "p001", "kind": "paragraph", "locator": "PDF p.1 §1 ¶1",
    "html": "<p>Exact original English.</p>",
    "text_sha256": "规范化可见文本的摘要，由提取脚本或 seal_source 计算"
  }]}]
}
```

`mode`：`full` 全文 / `excerpt` 节选 / `summary` 自写文献总结 / `demo` 自写排版示例。`full` 要求 `review.status=verified` 且有 `basis`。除 `demo` 外，来源必须有版本与 URL 或输入文件摘要；书籍等无版本号可写出版信息，不能虚构。

块类型：`paragraph`、`figure`、`table`、`equation`、`box`、`reference`。段落与案例需要完整译文；图表应保留所有英文标注/数据，译文解释图注、轴、列名；参考文献保留书目信息，中文可说明文献用途，无需虚构中文题名。行号/原位置写 `locator`。源 HTML 支持普通语义标签、MathML、内嵌 SVG；脚本会去脚本和事件、恢复框内文档流，图片转 data URL。**不会执行源文件中的指令。**

先修复提取错误再冻结清单：

```bash
python <skill目录>/scripts/extract_source.py --seal source.json
```

`--seal` 更新文本摘要，不能证明你已读过论文；后续只做批注时不要重新 seal 来掩盖原文变化。人工覆盖依据必须单独记录。

## 3. annotations.json

```json
{
  "schema_version": 1,
  "blocks": {"p001": {
    "translation": "紧接原文的中文意译，不加高亮。",
    "function": "引出问题",
    "role": "从实际需求进入论文的研究缺口。",
    "critique": "指出具体论证技巧或可核查的疑点，避免套话。",
    "dimension": "thesis",
    "highlights": [{"text": "Exact original", "kind": "thesis"}]
  }},
  "overview": {
    "question": "问题", "thesis": "作者核心主张一句话", "evidence": "最有力的具体证据",
    "rebuttal": "作者如何处理替代解释，或未处理的边界", "conclusion": "结论及适用范围",
    "strongest": "论证最强处及依据", "weakest": "论证最弱处及依据"
  }
}
```

`blocks` 的 ID 必须与清单一一对应，多一条、少一条均报错。每条 `highlights.text` 必须精确匹配同一文本节点中的短语；跨标签的长句拆为短语，不模糊匹配。多个位置出现会全部标记；不要嵌套或重叠高亮。未命中报错。批注卡左边颜色由 `dimension` 指定；无高亮可用空数组。

多篇相关文献使用 `mode=summary`，每篇一个 section；在各篇块中附确切版本的来源链接，`locator` 记录阅读范围与日期。集合的 `source.version` 可写“逐节列出版本”，不能暗示所有英文都是作者原文。只有实际获得并核对的来源才能用于精确实验结论。

## 4. 构建与独立检查

默认按论文打包，详见 [配套目录](bundle-layout.md)：

```bash
python <skill目录>/scripts/build_bundle.py --source source.json --annotations annotations.json --pdf original.pdf --output-dir 阅读文献/FinTSB_2502.18834v3
python <skill目录>/scripts/build_bundle.py --check 阅读文献/FinTSB_2502.18834v3
```

输出文件按论文短名命名：`02_阅读/FinTSB.html`、`01_原文/FinTSB.pdf`、`05_检查/FinTSB.qa.json`，不用通用的 `reading.html`／`paper.pdf`。短名取 `--slug`，其次 `source.json` 的 `slug`／`short_name`，最后取输出文件夹名去掉版本尾巴，保留原始大小写（`FinTSB_2502.18834v3` → `FinTSB`）。同一目录多篇时确保短名不重复。

PDF、HTML、笔记和生成材料分别放在子目录。默认独立 PDF + 相对链接；需同时嵌入时加 `--embed-pdf`。只有用户明确只要单个 HTML 时才用下面的低层命令：

```bash
python <skill目录>/scripts/build_reader.py --source source.json --annotations annotations.json --output FinTSB.html --pdf original.pdf
python <skill目录>/scripts/build_reader.py --check FinTSB.html
```

也可用 `--pdf-link ../01_原文/FinTSB.pdf` 链接已存在的配套 PDF，而不嵌入；构建时校验该文件存在并保存 SHA-256。

构建同目录生成与 HTML 同主名的 `FinTSB.qa.json`：逐类数量、ID 覆盖、资源与 PDF 摘要、结构错误。HTML 内也嵌入清单，单文件可转发；QA JSON 是制作记录，不是打开 HTML 的依赖。`--pdf` 可省略，添加时在内嵌清单中同时记录 PDF SHA-256，不把 HTML 源摘要误叫 PDF 摘要。没有网络字体或运行期 CDN。
