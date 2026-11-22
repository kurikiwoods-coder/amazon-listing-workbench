# CSV、JSON 和 Excel 导入格式

适用于 Amazon Listing 工作台 V1.3.2。一件商品只需提供一段文案，可上传到 **快速评分** 直接分析。想让系统分别评估标题、五点和 A+，在文件中标出对应字段即可。也可在已有项目的 **编辑 / 导入** 使用同样的文件。保存的商品页可直接上传，步骤见 [README](README.md#用浏览器保存商品页并导入)。

## 1. 三种格式通用的要求

- **单件商品**可以省略 `ASIN`。填写时请提供有效号码。如果项目已登记 ASIN，文件里的站点和号码要与项目一致。
- **多件商品**按站点和 ASIN 匹配。上传前在 **快速评分** 选择站点并填目标 ASIN，或直接粘贴 Amazon 商品详情链接。文件中的目标组合须唯一。只有 ASIN 的行默认用所选站点，多站点文件建议加上站点列。
- **空字段留空**。CSV 和 Excel 可留空单元格，JSON 可省略键或填 `null`。已有项目里，空值会保留原有字段内容。需要标记某项已确认不存在时，在导入编辑界面设置资料状态。
- CSV、Excel、JSON、文本和截图最多 **8 MB**。已保存的 HTML/MHTML 网页最多 **32 MB**。未分段原文最多 **32,000 字符**，其他单个字段最多 **12,000 字符**。多行标题和 Item Highlights 会合并成一行。五点可以一列多行、每条分列，或用 JSON 数组保存。

## 2. 字段名和内容怎么填

| 页面内容 | CSV / Excel 表头 | JSON 键名 | 填写方式 |
| --- | --- | --- | --- |
| 商品编号 | `ASIN`、`商品 ASIN` | `asin` | 10 位字母和数字或完整 Amazon 商品详情链接。 |
| 站点 | `Marketplace`、`站点` | `market` | 例如 `US`、`UK`、`DE` 或 `JP`。可填站点域名，例如 `amazon.co.jp`。 |
| 商品标题 | `Title`、`Product Name`、`商品名称`、`标题` | `title` | 商品标题原文。 |
| 商品亮点 | `Item Highlights` | `highlights` | 标题附近独立的 Highlights 原文，一行即可。 |
| 五点描述 | `Bullet Point 1`、`卖点 1`、`五点`、`Bullets`、`Features` | `bullets` | 分列时每列一条。合用时，单元格里每行一条。JSON 可用数组。两种写法同用时，优先读合并的字段。 |
| A+ 内容 | `A+`、`A+ 内容` | `aplus` | 填 A+ 页面文字，可分段。图片文字也可通过 A+ 截图导入。 |
| 普通描述或未分段文案 | `Description`、`Listing`、`文案正文`、`商品描述` | `raw_text` | 一段描述或整份 Listing。 |
| 公开商品规格 | `Product Information`、`公开商品规格` | `public_specs` | 商品页表格和 Technical details。每行一个项目。 |
| 后台属性 | `Attributes` | `attributes` | Seller Central 的产品字段。按 `属性: 值` 填写，每行一项。 |
| 后台搜索词 | `Search Terms`、`generic_keyword` | `search_terms` | 每行一个词组，也可用逗号分隔。 |
| 目标关键词 | `Keywords` | `keywords` | 每行一个词组，也可用逗号分隔。 |
| 价格 | `Price` | `price` | 保留原币种，例如 `$29.99`。 |
| 评论摘要 | `Reviews` | `reviews` | 填简要文字，可换行。 |
| 商品问答 | `Q&A` | `qa` | 填问答原文或摘要。 |
| 竞品 | `Competitors` | `competitors` | 填竞品 ASIN 或问题，每行一项。 |

缺少后台资料时，Search Terms、目标关键词和后台属性可以全部留空。工作台会用前台原文生成独立候选。确认真实字段时，再用卖家导出或自有目标词表导入。

品牌、类目、SKU 和产品类型可在工作台的 **商品资料** 里维护。以下示例使用演示文案和虚构 ASIN，使用时换成项目对应的资料。

## 3. CSV（`.csv`）

1. CSV 推荐用 **UTF-8**，支持逗号、分号或制表符分隔，也可上传 `.tsv`。前面几行可留空或放简短说明，紧接表头的每行写一件商品。
2. 字段内容带英文逗号、双引号或换行时，用英文双引号包住整个字段。字段里面的英文双引号写成两个，例如 `"Size: 10"" wide"`。
3. 保持每条数据与表头列数一致。分列写五点时，按 `Bullet Point 1`、`Bullet Point 2` 的顺序排列，有几条填几条。

```csv
ASIN,Title,Item Highlights,Bullet Point 1,Bullet Point 2,A+,Attributes
B0EXAMPLE1,Foldable Laptop Stand,Folds flat for storage,"Fits 11, 13 and 15-inch laptops",Raises the screen for video calls,Use this stand on a level desk.,Material: Aluminum
```

在 Excel 里做好的表可以另存为 **CSV UTF-8（逗号分隔）**。

Oxylabs 开源脚本的 `amazon_products.csv` 可直接导入。工作台识别 `asin_code`，同时核对 `url` 的站点和 ASIN。

只有一件商品时，可以直接传一个单列 CSV，列名写 `文案` 或 `Description`，后面的每行作为同一份原文。整段无表头的单列文案也可以直接初筛。

## 4. JSON（`.json`）

1. 保存成 **UTF-8**。单件商品推荐直接用一个对象 `{}`。多件商品使用数组 `[]`，数组中每个对象对应一件商品，并带有 `asin`。
2. 键名和文字用英文双引号。逗号写在各项之间，最后一项后直接用 `}` 或 `]` 收尾。多行文字可用 `\n` 表示换行，也可将五点写成字符串数组。

```json
{
  "asin": "B0EXAMPLE1",
  "title": "Foldable Laptop Stand",
  "highlights": "Folds flat for storage",
  "bullets": [
    "Fits 11, 13 and 15-inch laptops",
    "Raises the screen for video calls"
  ],
  "aplus": "Use this stand on a level desk.",
  "attributes": "Material: Aluminum"
}
```

也兼容 `fields`、`listing`、`product`、`data`、`items` 等常见包装。字段可填字符串、数组或带 `text`、`value` 的对象。最简 JSON 可以是一个用双引号包起来的文案字符串。导入后的确认状态在工作台内设置。

Nexscope Product Detail 的原始 JSON 可以直接上传。文件含多件商品时先选择站点并输入目标 ASIN。若响应省略商品链接和站点，工作台使用上传时选定的站点。

```json
{
  "asin": "B0EXAMPLE1",
  "fields": {
    "title": {"text": "Foldable Laptop Stand"},
    "bullets": {"text": "Fits 11, 13 and 15-inch laptops\nRaises the screen for video calls"}
  }
}
```

## 5. Excel（`.xlsx`）

1. 保存成 **`.xlsx`**，保存前切到放 Listing 数据的工作表。提供目标 ASIN 时，活动表找不到该商品，工作台会尝试匹配其他表。
2. 可以按列排表，每列一个字段、每行一个商品，表头前可有空行或说明。单件商品也可以用两列竖排，左边写字段名，右边写文案。
3. `ASIN` 列设为**文本**，避免纯数字编号的前导零丢失。若文案由公式生成，导入前可复制并粘贴为值。需要在一个单元格中写多条要点时，Windows Excel 用 `Alt+Enter` 换行。
4. 当前版本读取第 **1 至 2,501 行**（含表头）及前 **100 列**。将要导入的数据放在这个范围内。

| ASIN | Title | Item Highlights | Bullet Point 1 | Bullet Point 2 | A+ | Attributes |
| --- | --- | --- | --- | --- | --- | --- |
| B0EXAMPLE1 | Foldable Laptop Stand | Folds flat for storage | Fits 11, 13 and 15-inch laptops | Raises the screen for video calls | Use this stand on a level desk. | Material: Aluminum |

## 6. 在工作台导入并评分

- **只想评分**。在 **快速评分 → 上传网页或 Listing 文件** 选择文件。预览核对后点击 **原文已核对，开始评分**。上传识别到的 ASIN 会自动入库。未附 ASIN 的快评资料可手动留档。
- **已有项目需要留档**。打开目标项目，进入 **Listing 诊断 → 编辑 / 导入**。核对预览后点击 **应用识别字段**，确认各字段的资料状态，保存原文快照，再运行评分。

| 常见情况 | 怎么处理 |
| --- | --- |
| 提示多行或 ASIN 不匹配 | 对照当前项目和文件的 `ASIN` 列，确认这一号码只对应一行。也可另存只含当前商品的一份文件。 |
| 文案出现在未分段原文中 | 可以直接初筛。若要得到字段评分，可使用第 2 节的表头或 JSON 键名。 |
| CSV 出现乱码或错列 | 另存为 UTF-8，检查英文逗号分隔和带逗号字段的双引号。 |
| Excel 读到空白或另一份表 | 回到正确的工作表，保存后重新上传。多商品的文件可用目标 ASIN 锁定。 |
