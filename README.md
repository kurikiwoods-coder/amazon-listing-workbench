# Listing Studio · Amazon Listing 工作台

**V1.3.2** 增加美国站公开商品归档。没有本地资料或已配置的商品接口时，输入 US ASIN 即可查询 img.team 已发布的样本。预览保留来源链接与原始采集时间。版本变化见 [更新日志](CHANGELOG.md)。

## 先用一个商品试一遍

1. 使用发布包时，解压后运行 `start-workbench.cmd`。从仓库下载源码时，先[构建界面](#从源码重建界面)，再运行启动脚本，在打开的工作台进入 **快速评分 → 填入示例 → 粘贴并评分**。也可在 **上传网页或 Listing 文件** 中选随包的 [营养秤示例网页](examples/nutrition-scale-demo.html)，体验图片转录和板块识别。两个例子均使用演示数据。
2. 在 **原始 Listing → 前台原文** 展开各板块，也可点 **下载原文 TXT**。单独的「商品描述」为空时，汇总仍会列出这次读取到的其他前台文案。
3. 在 **后台字段建议** 看三个栏目。点击候选下方的引文，可以回到具体字段和段落。点 **留作备选** 后可导出清单。ASIN 项目按原文快照保存留用和忽略记录。快速评分在当前页面保留选择。
4. 以示例营养秤为例，原文 `Meal prep` 可产生 `meal preparation` 搜索词候选。公开规格 `Capacity: 5 kg` 可产生容量候选。A+ 对比表里的另一款秤为 10 kg，该行只在前台原文中展示。后台真实值可从自己的卖家导出中录入。候选不会改变实得分、覆盖率或产品事实。

要部署到 EC2，从 [AWS_AMI.md](AWS_AMI.md) 开始。发布包内含 Packer 配置和工作台运行包。实际 AMI 由你的 AWS 账号构建并获得对应区域的 AMI ID。

## 在本机运行

安装 Python 3.11 或以上版本。Windows 用户可双击 `start-workbench.cmd`。脚本会在首次运行时创建本地环境并安装依赖。看到终端显示 `Uvicorn running on ...` 后，打开该行显示的地址，默认是 [http://127.0.0.1:8000](http://127.0.0.1:8000)。使用期间保持启动窗口开启。

手动启动命令适用于已配置 Python 环境的用户。

```bash
python -m pip install -r requirements.txt
python -m backend.serve
```

项目、事实、任务和版本记录保存在 `data/workbench.sqlite3`。默认服务仅监听本机地址。备份 SQLite 文件前应先停止服务。设置 `LISTING_SEED_DEMO=0` 可在首次启动时跳过示例。

在 VS Code 中可用 **文件 → 打开文件夹**，选择解压后的 `listing-workbench` 文件夹。发布包附有构建好的页面。从仓库取得源码时按本文末尾的命令生成页面。升级时先退出旧工作台，再把旧目录的 `data/workbench.sqlite3` 复制到新目录的 `data` 下。没有站点信息的旧采集记录会标为美国站。如旧版使用了模型或 Nexscope，还需将自己的 `.env` 复制到新目录。启动新版本后，刷新页面并确认左侧显示 **V1.3.2**。

### 连接不上工作台

如果显示 `Failed to fetch` 或「浏览器连不上本机工作台」，先看启动窗口是否出现 `Uvicorn running on ...`，再在这个地址后加 `/api/config` 打开，例如 [http://127.0.0.1:8000/api/config](http://127.0.0.1:8000/api/config)。看到包含 `amazon_playwright` 的 JSON 后，用同一个地址打开工作台首页并重试。

若启动窗口报端口已占用，先退出仍在运行的旧版工作台，再启动新版本。若报安装或启动失败，可按终端显示的错误定位。工作台网页和 API 由同一个服务提供，浏览器应打开终端显示的地址。

## 用浏览器保存商品页并导入

此入口用 `start-workbench.cmd` 启动即可。采集浏览器收到 Amazon 验证页时，工作台会停止在线请求，并检查有无同站点的历史原文。需要补充资料时，可点击提示下方的 **用浏览器打开商品页**。等商品标题和正确变体显示后再保存。

1. 在 **快速评分 → 采集或导入** 输入 ASIN 或商品链接，点击 **用浏览器打开商品页**。已有项目也能在 **编辑 / 导入** 使用这项入口。
2. 在 Edge 或 Chrome 确认站点和当前选中的颜色、尺寸或型号。等标题、五点和需要的 A+ 内容出现在页面上，按 **Ctrl+S**，保存类型选 **网页，仅 HTML** 或 **单文件网页（MHTML）**，文件名可直接用 ASIN。
3. 回到工作台，点击 **上传网页或 Listing 文件**。优先上传 `.html`、`.htm`、`.mhtml` 或 `.mht` 文件。工作台也会按内容识别常见网页导出。网页上限 32 MB，表格、文本和截图上限 8 MB。需要识别图片中文字时，选 MHTML 或 SingleFile。仅 HTML 通常只带图片链接。
4. 核对预览中的字段和 ASIN，再点击 **原文已核对，开始评分**。若页面选中的是另一个变体，提示会显示实际 ASIN。要分析该变体，可在快速评分清空输入框，再上传网页。

还可以用开源 **SingleFile** 把已经正常打开的商品页保存为一个 HTML，再上传同一份文件。工作台可从 SingleFile 的原始地址备注核对站点和 ASIN。 [SingleFile GitHub](https://github.com/gildas-lormeau/SingleFile)

**五点（Bullet Points）** 是详情页通常位于 **About this item** 的卖点列表，逐条保留。Amazon 有时会在这里附加来自属性的条目。字段评分会提示去 Seller Central 核对实际录入条数，候选文案仍按当前发布规则检查。[Amazon 五点指南](https://sellercentral.amazon.com/help/hub/reference/external/G10471) · [Amazon 对附加条目的说明](https://sellercentral.amazon.com/seller-forums/discussions/t/804d6a1c-d630-4879-b548-3235dac90940)

**Item Highlights** 是部分商品在标题附近展示的独立短文案，可以补充材质和使用场景。工作台分别读取 Highlights、五点和 A+，只有页面出现可识别的原文时才记录该字段。[Amazon Item Highlights 指南](https://sellercentral.amazon.com/gp/help/external/YTR6SYGFA5E3EQC?mons_sel_locale=en_US)

### 商品页板块

| Amazon 页面资料 | V1.1.0 的处理方式 |
| --- | --- |
| 标题、品牌、选中的型号 | 保留商品身份和原文，按站点核对 ASIN。不同颜色、尺寸或型号有各自的变体标识。 |
| 主图、视频和品牌故事 | 品牌故事文字在 A+ 汇总里注明来源。主图与视频可通过上传对应截图补充。 |
| 标题附近的 Item Highlights | 读取明确标注的补充短语。只有 Top highlights 规格卡或标题里有竖线时，保留原字段，提示核对。 |
| About this item | 逐条读取五点，去除展开按钮等界面文字。 |
| Product Description 和 A+ | 分别保存商品描述和多个 A+ 模块。A+ 逐段标注网页正文、图片替代文字或 OCR 转录，并标出可识别的品牌故事与多商品对比。 |
| Product information / Technical details | 作为公开商品规格单独保存，可引用于买家问题。 |
| Buy Box、评论和 Q&A | 可读价格、页面生成的评论摘要作参考。Q&A 可手动补充。 |
| 推荐商品和广告 | 可用于记录实测位置。评分采用目标商品的原文和事实。 |
| 后台属性、Search Terms 和 SQP | 真实记录来自卖家提供的资料。前台可另生成待审的字段候选。SQP 查询量、点击和排名需用相应报告核对。 |

来源见 [Amazon Listing 指南](https://sell.amazon.com/blog/amazon-product-listings) · [Item Highlights 说明](https://sellercentral.amazon.com/gp/help/external/YTR6SYGFA5E3EQC) · [Listings Items](https://developer-docs.amazon.com/sp-api/reference/getlistingsitem) · [A+ 内容](https://developer-docs.amazon.com/sp-api/reference/getcontentdocument) · [SQP 报告](https://developer-docs.amazon.com/sp-api/docs/report-type-values-analytics)。

## 已授权商品 API 和本地归档

- 在服务端配置 `LISTING_NEXSCOPE_API_KEY`。Windows 本机部署可在工作台根目录新建或编辑 `.env`，加入 `LISTING_NEXSCOPE_API_KEY=你的授权密钥`。EC2 配置见 [AWS_AMI.md](AWS_AMI.md#配置和排查)。重启后在 **快速评分** 或 **编辑 / 导入** 点 **用 Nexscope 刷新商品资料**。输入 10 位 ASIN 时使用所选站点，粘贴完整链接时从域名识别。任务会出现在左侧 **最近**，请求走 Nexscope 账号的接口额度。 [Nexscope Product Detail](https://www.nexscope.ai/api-docs/amazon-product-detail)
- 已有 Nexscope Product Detail JSON 可以直接上传。其 `aboutItemFivePoint` 对应五点，`itemSpecifications` 和 `productDetails` 对应公开规格。若响应只带混合的 `aboutItem`，工作台把它保留为未分段原文。
- GitHub 上 Oxylabs 开源工具输出的 `amazon_products.csv` 也能导入。CSV 带标题、商品链接、ASIN、图片链接和价格。Oxylabs 的完整商品 API 另行授权。 [Oxylabs 仓库](https://github.com/oxylabs/amazon-scraper)
- 成功采集和识别出的网页或商品数据会保留本地归档。输入同站点和 ASIN 后，点 **获取商品资料（优先归档）**。工作台也可以从 ASIN 库的当前快照读取已保存的公开原文。预览会显示来源和时间，可直接评分，也可在项目里补齐原本未采集的字段。
- **读取本地商品归档** 仅查找本机资料。没有归档时，主入口优先使用已配置的 Nexscope 接口。未配置时，美国站查找下方的公开资料，其它站点按启用情况使用 Playwright。

### 美国站公开归档

- 选择 **美国**，输入 ASIN 后点击 **获取商品资料（优先归档）**。遇到 Amazon 验证或已经在看本地资料时，也可点 **查公开归档**。工作台只向 img.team 读取该 ASIN 的已发布详情记录。 [公开归档来源说明](https://img.team/guides/amazon-image-research)
- 结果显示源站标题、卖点、公开规格以及原始采集时间。评分按实际取得的模块计算。A+ 图库、评论和价格不当作 Listing 原文导入。编辑项目时，公开归档用于补齐未采集字段。
- 该资料库覆盖它已收录的美国站样本。无结果时，可导入已保存的 HTML、MHTML 或授权接口返回的资料。 [SingleFile 项目](https://github.com/gildas-lormeau/SingleFile) · [Amazon SP-API 模型](https://github.com/amzn/selling-partner-api-models)

## 按字段改写与修改意见

1. 在左栏打开 **AI 改写 → 粘贴并改写**。粘贴项目中的一条五点，或粘贴带 `Title`、`Item Highlights`、`About this item`、`A+` 等标签的多段文字。可在 **优先回答的买家问题** 填一条真实的 Alexa 问句，再点 **识别并改写**。
2. 工作台会显示识别出的字段。匹配当前原文时会显示具体要点或段落。两处原文相同或缺少字段线索时，可手动选字段后再运行。没有打开 ASIN 项目时，也可从左栏进入并粘贴文字。
3. 对照原文和改写稿，查看哪句回答了买家问题和可核对的依据。匹配项目位置的标题、Item Highlights、五点和 A+ 可保存为候选版本，再审核、复评。
4. 在项目的 **Listing 诊断 → 问题诊断与修改意见** 查看原句、建议文案与修改理由。点 **打开并改写** 可带入对应位置。遇到事实缺口时，页面展示需要核实的问题。缺少原文的模块可从 **编辑 / 导入** 补录。

标题先说明商品类型与核心差异。Item Highlights 补充买家决策需要的信息。五点尽量每条回答一个问题，把使用场景、动作、结果和条件写在一起。A+ 用可读的文字解释操作顺序和适用范围。微信原文当前无法读取，本版依据明确引用它的开源项目和同作者公开的实操文章整理。 [Alexa Listing 优化项目](https://github.com/liangdabiao/amazon-listing-alexa-optimizer) · [Regan 的实操文章](https://www.amz123.com/t/w1IB1lvA)

配置模型后，开启 **AI 改写与语义复核**。工作台先按字段生成文案，再单独核对数字、单位、适用范围和答句能否追溯到原文或已确认事实。以演示营养秤为例，会分别说明首次用 App 设置和保存食物，以及后续在秤上选择已保存食物后查看热量和蛋白质。模型复核未通过时显示本地结果。候选稿仍需完成人工事实和发布检查。

## 交给 Alexa Listing 优化 Skill

本工作台配套的 [Amazon Listing 改写 Skill](https://github.com/kurikiwoods-coder/amazon-listing-rewrite-skill) 可以独立使用。将下面导出的 Markdown 或手动粘贴的原文交给 AI，即可按买家问句诊断、改写并复评。仓库内附中文使用说明和示例。

在 ASIN 项目的 **Listing 诊断** 点击 **导出 Alexa Skill 输入**。Markdown 包含当前 Listing 快照、字段核验状态、站点规则、已确认产品事实、买家问句和可复用的诊断引用。在支持 Skills 的工具中，可以和 `amazon-listing-alexa-optimizer` 一起使用。

建议先导入真实 Alexa 问句再诊断。比如营养秤的「第一次是否需要 App 设置」和「设置后日常称量时能否关闭 App 直接读秤屏」应分别找到原文证据。改写后用同一组问句复评，Alexa 的回答和商品卡继续在 Amazon 实测记录。 [Alexa Listing 优化 Skill](https://github.com/liangdabiao/amazon-listing-alexa-optimizer) · [Amazon 对 Alexa for Shopping 的介绍](https://www.aboutamazon.com/news/retail/alexa-for-shopping-ai-assistant)

## 输入 ASIN 自动提取

1. Amazon 常规使用条款限制自动提取。浏览器采集适用于有相应许可的场景。[Amazon 使用条件](https://sellercentral.amazon.com/help/hub/reference/external/201824360)
2. Windows 用户双击 `start-with-playwright.cmd`。首次启动会安装 Python Playwright 和 Chromium。已经运行普通工作台时，先退出旧进程。使用 VS Code 时也可以在终端运行该脚本。
3. 在 **快速评分 → 采集或导入**，或 **ASIN 库 → 打开项目 → 编辑 / 导入**，点 **获取商品资料（优先归档）**。需要查看网页的最新版本时可点 **实时刷新商品页**。仅输入 10 位 ASIN 时使用所选站点。商品详情链接会以自身域名识别站点。已登记项目会带出自己的站点和 ASIN。
4. 在左侧 **最近** 查看队列状态。工作台服务保持运行时，可以关掉编辑窗口、切换项目或刷新页面。点任务可重新打开进度或预览。右键或点行尾的更多菜单可以删除任务及预览。移除任务时，进行中的浏览器请求在返回或超时后结束。
5. 核对预览中的标题、五点、商品描述、公开规格、Item Highlights、A+ 和价格。显示项取决于当前页面可读的内容。在编辑窗口点击 **核对原文并应用 → 保存原文快照**，再点 **运行评分**。在快速评分可直接点击 **原文已核对，开始评分**。

遇到验证码、登录要求、访问受限或 ASIN 跳转时，系统停止本次在线采集。有同站点、同 ASIN 的归档时，会显示失败原因并同时打开历史预览。没有历史资料时，可用已授权接口或上传从常用浏览器保存的正常商品页。

### 采集失败排查

| 页面提示 | 对应情况及处理 |
| --- | --- |
| 本机 Chromium 启动超时 | 浏览器尚未启动。检查工作台终端，退出旧进程后用 `start-with-playwright.cmd` 启动。 |
| 连接 Amazon 某站点超时 | 30 秒内没有收到商品页响应。点 **用浏览器打开商品页**，在同一台电脑检查访问是否正常。网页可打开时可保存并上传。 |
| 已收到商品页内容，但未识别到商品标题 | 已收到响应。打开同站点的商品页检查商品是否可见，可上传原文继续评分。 |
| 实际打开的站点或 ASIN 与目标不同 | 提示会显示目标和实际地址中的 ASIN，并在可识别时显示页面选中的变体。请核对站点和规格后保存正确的详情页。 |
| HTTP 404、403、429 或验证码 | 查看右侧显示的站点及状态。404 时检查该 ASIN 是否在所选站点上架。遇到访问限制或验证时可上传原文。 |

收到页面响应后会等待商品正文。页面加载缓慢时，已读到的标题和详情可以预览并核对。

在 macOS、Linux 或自选 Python 环境中，从项目目录运行以下命令。也可将 `LISTING_AMAZON_PLAYWRIGHT=1` 加到项目的 `.env` 后使用 `python -m backend.serve` 启动。Linux 的 Chromium 系统依赖安装方式见 [Playwright 文档](https://playwright.dev/python/docs/browsers)。

```bash
python -m pip install -r requirements.txt "playwright>=1.50,<2"
python -m playwright install chromium
LISTING_AMAZON_PLAYWRIGHT=1 python -m backend.serve
```

## 自动入库与临时评分

1. 在 **快速评分** 粘贴原文，或在 **采集或导入** 上传保存的网页、CSV、Excel、JSON、文本或截图。表头可用常见中英文名称。整段文字也可作为一个模块直接评分。多商品文件先填写目标 ASIN。[文件格式说明](IMPORT_FORMATS.md)
2. 成功采集或导入识别到 ASIN 后，以 **站点 + ASIN** 查找项目。新组合会自动建项目并保存待核实原文。快速导入已有组合会把新读到的内容补进未采集字段，保存新快照。若新 A+ 完整保留旧版待核实段落，也可补齐它。编辑窗口内先应用识别字段，再点保存。使用 CSV、Excel 或 JSON 时，可提供 `Marketplace`、`站点` 或完整商品链接来区分同号商品。
3. 点击 **粘贴并评分**，或核对导入预览后点击 **原文已核对，开始评分**。快速评分结果在当前页面显示。手动粘贴可点击 **保存文案到 ASIN 库** 留档。项目内运行的评分会随项目保存。

## 管理 ASIN 库

在 **ASIN 库** 搜索、筛选或打开项目。每行的 **删除** 仅针对选中的项目。二次确认后，项目及其原文、事实、评分、改写和历史会一并删除。

首页可打开营养秤演示项目。它的产品、ASIN 和测试记录都是合成样本。进入 **AI 改写** 选择方案 B，生成候选稿，接受或编辑变更后点击 **保存并复评**。版本页可以追溯原稿、审核、复评和实际上线记录，报告支持 Markdown、Excel 和 PDF。

在已有项目里，可通过 **Listing 诊断 → 编辑 / 导入** 导入资料并手动保存新快照。文本可以按下例分段。

```text
Title: DemoBrand Kitchen Scale
Item Highlights: Weigh food in grams or ounces
Bullets:
1. Subtract bowl weight with the tare function
2. Wipe the surface with a damp cloth
A+:
Use the scale during meal prep
```

### A+ 图片怎样变成文字

A+ 可以包含品牌图文、视频和对比表。HTML 正文直接读取。图片替代文字单独标注。印在图里的字可通过本机 Tesseract 转录。图片未随文件保存，或没有合适的语言包时，预览会提示缺少什么。[Amazon A+ 介绍](https://sell.amazon.com/tools/a-content) · [Tesseract GitHub](https://github.com/tesseract-ocr/tesseract)

- Windows 安装 Tesseract 后，在新开的 VS Code 终端运行 `tesseract --list-langs`。英语页面需要 `eng`，德语需要 `deu`，日语需要 `jpn`。如需指定安装位置或识别语言，可在 `.env` 填 `LISTING_EDITOR_OCR=C:/Program Files/Tesseract-OCR/tesseract.exe` 和 `LISTING_OCR_LANG=eng+deu`，重启工作台。普通网页文字和本地候选可直接使用。
- 重新抓取前，可先在正常商品页滚动到 A+ 并等图片出现。已有 HTML/MHTML 上传只读取文件内的资料。每页自动 OCR 最多尝试 4 张图片。其余图片可裁剪后分开上传。
- 上传 A+ 截图前，在 **指定截图所属板块** 中选 **A+ 内容**。识别文字会出现在该字段。已有项目可打开原截图核对，缺少 OCR 时可手动转录。图像文字、型号和关键数字均需人工核对。

### 后台字段建议怎样生成

| 栏目 | 可以从前台整理的内容 | 取得真实值所需的资料 |
| --- | --- | --- |
| Search Terms | 同义说法、缩写和标题以外的补充词 | 自有 ASIN 在 Seller Central 的 Generic keyword 或卖家导出 |
| 目标关键词 | 品类名、使用场景和具体功能词组 | 运营目标词表。实际买家查询和表现见有权限的 SQP 等报告 |
| 后台属性 | 公开规格中的属性名、值、单位，或有原文可核对的材料等属性 | 同站点、同 SKU 的卖家后台和类目字段模板 |

后台 Search Terms 对买家不可见。工作台生成的是运营可留用的备选清单。没有足够证据的栏目会说明需要补什么。已录入的字段保留其原文，在类目不适用时也不会生成对应候选。[Amazon 后台 Search Terms 说明](https://sell.amazon.com/blog/amazon-seo) · [Selling Partner API 模型](https://github.com/amzn/selling-partner-api-models)

未配置模型时，系统用原文词组、常见同义词和明确属性对生成本地候选。配置模型后点击 **用 AI 扩展建议**。模型收到站点以及带字段和段落号的前台摘录。程序逐条检查引用，按快照缓存通过检查的结果。原文快照、产品事实和可发布改写分别保存。

## AI 语义模式

默认使用本地规则。类目问题模板、可验证词组、已确认事实及字段规则可在这个模式下运行完整流程。需要进一步判断同义表达和改写策略时，将 `.env.example` 复制为 `.env`，填入自己的模型接口密钥并重启。

```dotenv
LISTING_LLM_API_KEY=your_key
LISTING_LLM_BASE_URL=https://api.openai.com/v1
LISTING_LLM_MODEL=gpt-4.1-mini-2025-04-14
```

也可以在 Windows 使用本机 Ollama。安装并启动 Ollama，打开终端运行 `ollama pull qwen2.5:7b`，下载完成后用 `ollama list` 确认模型名。在工作台根目录的 `.env` 添加下面两行，然后重启工作台。这种配置连接运行工作台那台机器的本地模型，云端密钥可留空。也可换为本机已安装且支持结构化输出的模型。[Ollama GitHub](https://github.com/ollama/ollama) · [Windows 使用说明](https://docs.ollama.com/windows) · [示例模型](https://ollama.com/library/qwen2.5:7b)

```dotenv
LISTING_LLM_PROVIDER=ollama
LISTING_OLLAMA_MODEL=qwen2.5:7b
```

外部模型接口需支持 OpenAI 兼容的 Chat Completions 和 JSON 模式。密钥留在服务端。选择 AI 语义评分时，程序将本次所需的原文、问句、产品信息及已确认事实发送给模型。评分保留模型、提示词和规则版本。后台任务可取消，失败时可重试。

整份 Listing 改写按模型或本地策略选择已确认的事实条目。候选版本以已核实的英文措辞作为发布检查依据。若要采用新写法，先核对依据，在事实库保存该准确措辞，再基于更新后的事实做基准评分和改写。审核及事实和规则校验通过后，可以复制到 Seller Central 人工发布。

## 计分与验证

- 标题、Item Highlights、五点、A+、商品描述、公开规格、后台属性、Search Terms 和目标关键词分别计分，满分 100。总分为本次已评分模块的平均值。只有五点时，总分就是五点的分数。
- 未取得或无内容的模块折叠在 **未评分模块**，可以补录编辑。后台字段要有实际资料才评分。采集得到的文本可以直接评估表达质量，核验状态会显示在模块分数旁。
- 诊断定位到当次提供的正文。明确的规则或事实冲突优先处理。其它问题展示具体改法、可核对的证据和候选文案。用 AI 评分时会为优先问题生成和复核改写。
- 复评对照同一批模块。两次纳入的模块有变化时，分别列分数和覆盖率，总分差留空。历史八维评分可以查看和复评，重新运行评分即可采用新口径。
- 问句中的高优先级按双倍权重计。无法判断的问句从可判断分母中剔除，界面同时保留完整问句数量。
- 规则可按站点、产品类型、类目和生效日期配置。内置的美站非媒体类目规则检查标题 75 字符、Item Highlights 125 字符和最多五条要点。其他站点默认使用通用文案评分。发布检查需要配置对应站点的适用规则。规则来源见 [Amazon 美国站标题更新](https://sellercentral.amazon.com/seller-forums/discussions/t/cd6a14da-a5c7-4f62-b886-7ead97407b3c) 和 [详情页规则](https://sellercentral.amazon.com/gp/help/external/G200390640)。
- 复评固定原评分的规则、事实、问句和模型版本。价格和评论量等市场信息仅为参考。真实 Alexa 推荐、转化和销量需独立观察。

### 验证命令

```bash
python -m unittest discover -s tests -v
```

测试包括 5 类 30 个合成回归样例，以及导入、复评口径、审核阻断和文件导出。模型 JSON 接口使用模拟响应测试。V1.3.2 完整发布包另附 `ASIN_calibration.xlsx`，包含五类共 30 个真实 ASIN 的待审样本和核对来源。正式校准需要为这些样本保存完整 Listing 快照并获得独立人工标注。真实模型效果也需以实际接口和标注样本复核。

拿到真实人工标注后，可运行 `python -m tests.calibration real-labels.json`。输入为 JSON 数组，每项包含 `product`、`listing`、`context`、`reviewer`、`human_score` 及 `human_p0`。其中 `human_p0` 为 `[{"field":"title","kind":"policy"}]` 这类记录。数据字段沿用 API 定义，空 P0 填 `[]`。输出包括正式评分的平均绝对误差、P0 字段和类型层面的精确率与召回率、重复评分一致率及临时评分数量。

采集队列、自动入库、删除任务和问题诊断使用模拟响应及导入样例验证。网页导入测试覆盖 HTML、MHTML、变体核对和字段归类。实时 Amazon 页面取决于访问条件和页面布局。批量诊断、竞品对比、Alexa 推荐实测和业务指标实验属于后续迭代。

## 从源码重建界面

后端使用 FastAPI 和 SQLite，前端使用 Next.js、React 和 TypeScript。从 GitHub 获取源码后，安装 Node.js 22 或以上版本。在项目根目录运行以下命令生成页面。构建完成后也可用 Windows 启动脚本。

```bash
npm ci --prefix frontend
npm run build --prefix frontend
python -c "from shutil import copytree; copytree('frontend/out', 'web', dirs_exist_ok=True)"
```

服务启动后可在 [API 文档](http://127.0.0.1:8000/docs) 查看数据结构。规则存放在 `backend/rules.json`，也可通过 `LISTING_RULES_FILE` 指定自定义规则配置。请用单进程运行本地任务队列。
