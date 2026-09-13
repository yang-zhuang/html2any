# 对齐说明（移植自三个原始脚本，原文保留）

本文档保留三个原始脚本文件头部的"与原始实现对齐"说明，移植进 `html2any` 包时
代码逻辑未动，这些背景信息是理解各方式行为的依据。

---

## 方式一：markdown（原 `html_to_markdown.py`）

给定一个 html 网址，保存为 markdown，并把页面里的图片下载到本地 imgs/。

流程：

    url --requests--> html --BeautifulSoup--> 正文节点 --markdownify--> markdown
                                          └--下载 <img> 到 imgs/，src 改写为相对路径

关键点：

1. `KeepAllImagesConverter`：markdownify 原生 `convert_img` 会把出现在表格单元格、
   链接、`<span>` 等 inline 上下文里的图片降级成纯 alt 文本（直接父节点不在
   `keep_inline_images_in`，默认只有 th/td 时）。实测 Wikipedia 正文里的 4 张图
   全部落在 `<td><a>/<span>` 里，因此全部丢失。这里覆盖成任何上下文都输出
   `![alt](src)`——图片已下载到本地，目标就是"一定要出现在 markdown 里"。
2. 不要用 `strip=["span"]`：markdownify 的 strip 会连同内容一起删掉，大量站点
   的正文文字都包在 `<span>` 里，删了会丢正文。
3. 抓取带显式重试：实测中遇到过瞬时的 SSLEOFError，网络抖一下不应导致整次任务失败。

---

## 方式二：images（原 `html_to_images.py`）

给定一个 html 网址，渲染成多张图片（纯图片，无正文文本）。

本文件完全照搬原始实现 `bid_generate/将标书进行结构化/3_根据目录对正文进行结构化_with_table.py`
的做法（L33-193），只把输入从「docx 转出来的 HTML 片段」换成「一个 html 网址」：

    原始实现                                  本文件
    ----------                                ----------
    HTML 片段                                  url --requests--> html --> 抽正文
      |                                          |
      +-- 套固定外壳 CSS（字体/底色/宽度）        +-- 同样的外壳 CSS
      +-- html2image 截图 size=(1000,10000)      +-- Chrome 无头截图
      +-- 四向裁白（PADDING 15/20/10/10）        +-- 同样的四向裁白
      +-- 空白行智能分页（MAX_PAGE_HEIGHT=720）  +-- 同样的智能分页

三条核心行为（与原始实现逐条对齐）：

1. 改字体、底色：渲染前把正文套进固定外壳
   `body { font-family: "Microsoft YaHei","SimSun",sans-serif; font-size: 14px;
   line-height: 1.6; background: white; margin: 20px auto; width: 760px; }`
2. 四向裁白：把四周大片空白裁掉，再补 PADDING_TOP=15 / BOTTOM=20 / LEFT=10 /
   RIGHT=10 的边距。
3. 智能分页（不会切断内容）：单张图超过 MAX_PAGE_HEIGHT=720 时要切，但不是按
   固定高度硬切，而是在目标切割线 ±SEARCH_MARGIN=80 的范围内寻找**空白行**
   （该行白像素占比 >= BLANK_ROW_RATIO=0.995）；优先取目标线下方最近的空白行，
   没有就取上方最近的；若该范围内没有一行是空白行，则退化为取"最干净"（白像素
   最多）的那一行。因此切割线总是落在文字行之间、图片之间，不会把一行字或一张图
   劈成两半。

另一种模式 `--image-mode page`（非原始实现，额外提供）：让浏览器排版引擎分页
（Chrome --print-to-pdf + PyMuPDF 逐页栅格化），页边界由排版引擎决定，同样不会
切断内容，且每张图尺寸严格固定。

---

## 方式三：structure（原 `html_to_structure.py`）

给定一个 html 网址，把它结构化成一棵 JSON 递归树 + 每个节点对应的图片。

对齐的原始实现有两个：

  A) `D:\Code\bid_generate\分析采购文件\3_根据结构化目录对采购文件正文结构化\main.py`
     —— 树的字段、content/clean_content 的语义、逐节点截图、表格单独分段截图、
        断点续传、分页常量，全部照搬自它。
  B) `D:\Code\ai_bid_writer\backend\app\bid_writer\subgraphs\cover\utils\doc_structure.py`
     —— 该文件是 A 的移植版且**去掉了截图**（snapshot_path 恒为 []），
        它的注释写明 "Ported from reference step 3, lines 196-243, without html2image"。

A 的输入是 docx，树来自 step2「用 LLM 读目录页截图提取出的目录树」。
网址没有目录页，所以本方式把这一环换成：**直接按页面自身的 h1~h6 层级递归建树**。
除此之外的每一环都与 A 对齐：

    A（原始实现）                             本方式
    ----------                               ----------
    docx --mammoth--> 整篇 HTML               url --requests--> html --> 抽正文
    用 LLM 提的目录树在 HTML 里定位标题        用页面自身的 h1~h6 层级建树      <== 替换 1
    标题之间切出 content                     同样：blocks[pos+1 : next_pos]
    clean_content = content 去表格 + 图片置空  完全相同
    （原版没有图片本地化这一环）              新增 --images（默认关）：下载到 imgs/   <== 替换 2
    每节点 html2image 截图 → snapshots/       每节点 Chrome 无头截图 → snapshots/
    表格单独成段，各段分别截图再连续编号        完全相同（split_html_to_segments）
    空白行智能分页（MAX_PAGE_HEIGHT=720）      完全相同（find_best_cut）
    断点续传（已有 {base}_part*.png 就跳过）   完全相同

与原版的五处差异（1 是必须换的，其余都是为了适配"网页"这一新输入 or 避免重复存图）：

  1. 树的来源：原版是 LLM 读 docx 目录页截图提出的目录树，本方式用页面自身 h1~h6。
  2. 非表格段保留所有标签：原版 split_html_to_segments 只把 p/div/ul/ol/h1~h6 收进
     非表格段，pre / blockquote / figure / dl 等会被静默丢掉；网页正文里这些很常见，
     丢了会少内容，所以改成"除 table 外一律保留"。
  3. 新增 strip_noise()：去掉编辑链接、占位空元素、不可见元数据属性、正文里的
     `<link>/<meta>/<base>`。原版输入是 docx 转出的 HTML，天然没有这些网页噪音；
     在 Wikipedia 这类 MediaWiki 站点上，这些东西占 content 体积约三成，
     而且 [edit] 链接会被真的渲染进截图里（实测：某节点首段 2951 字符只画出 54x48 的
     [edit]，其余全是藏在 data-mw 属性里的不可见 JSON）。
  4. imgs/ 改成开关且默认关闭：快照是把节点的 content 整体渲染成的位图，图片像素
     本来就会出现在 snapshots/ 里，再额外落一份原图等于同一份像素存两遍。实测
     langchain 那篇 imgs/ 14 个 1.54 MB、snapshots/ 22 张 2.29 MB，其中 imgs/001.png
     （580,550 B）原样出现在 snapshots/0_..._part1.png（1,251,279 B）里。只有确实需要
     "离线搬走 + 原图无损 + 原始格式（svg/gif/webp 在快照里都会变成位图）"时才用
     --images。注意：不加 --images 只是不落盘，chrome 渲染快照时仍会去远程拉这些图。
  5. 原版 TABLE_SNAPSHOT_MODE 的另一个分支被删掉了：它的注释写着 "remove"（去表格）|
     "separate"（表格单独截图），实际只实现了 separate。separate 渲染 content_html
     （含图，main.py:392），被弃用的那个分支才渲染 clean_html（无图，main.py:394）。
     本方式只保留 separate，所以"快照带图"是继承原版默认模式的，不是新引入的。

result.json 每个节点的字段（与原始实现完全一致，顺序也一致）：

    level         中文层级，"一级"=h1 … "六级"=h6
    raw_title     标题文字
    children      子节点列表（递归）
    content       本标题到下一个标题之间的 HTML 片段（含表格、含图片）
    clean_content content 去掉 <table>、<img src> 置空后的 HTML
    snapshot_path 本节点图片的文件名列表（相对 snapshots/ 目录）

---

## 移植进 html2any 包时随统一而变化的地方（有意为之，非疏漏）

1. 正文挑选统一为共享 `pick_main`：候选选择器取三个原实现的并集（多了
   `[role=main]`、`.markdown-body`），文本量阈值统一 200 字符（方式二原为 80）。
2. 显式 `--main` 未命中时三个方式统一为直接报错（方式二原实现静默退回整页 body）。
3. 目录名不再用 URL slug，改用 `run_id`（日期 + 归一化 url 的 8 位哈希）；
   断点续传改由 run 级 `meta.json`（记录各方式完成状态）+ structure 原有的
   part 文件级续传共同承担。
4. 三种方式共用一次抓取：`source.html` 落在 run 目录，重跑时直接复用。
