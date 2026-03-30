# 川渝医护招聘爬虫系统

针对川渝地区医院招聘公告的增量爬虫系统，支持多网站爬取、WAF自动绕过和增量数据存储。

## 新手先看

- 新手维护入口文档：[/Users/xiongtao/Documents/scaper_zhaopin/chongqing_medical_jobs_crawler/docs/新手维护指南.md](/Users/xiongtao/Documents/scaper_zhaopin/chongqing_medical_jobs_crawler/docs/新手维护指南.md)

## 功能特性

- **多网站支持**：通过配置文件支持多个招聘网站
- **WAF自动绕过**：自动检测和绕过Cloudflare等WAF防护
- **增量爬取**：基于发布时间实现增量数据采集
- **可配置解析**：使用YAML配置定义解析规则
- **SQLite存储**：轻量级数据库，支持去重和查询
- **监控报警**：失败时记录和通知

## 项目结构

```
chongqing_medical_jobs_crawler/
├── config/                    # 配置文件
│   ├── sites/                # 各网站配置
│   └── global.yaml          # 全局配置
├── core/                     # 核心模块
│   ├── crawler.py           # 爬虫引擎
│   ├── waf_detector.py      # WAF检测器
│   └── database.py          # 数据库操作
├── spiders/                  # 爬虫实现
│   ├── base_spider.py       # 基础Spider
│   └── medical_spider.py    # 医疗招聘Spider
├── models/                   # 数据模型
│   └── job.py               # 招聘职位模型
├── data/                     # 数据库文件
├── main.py                  # 主程序入口
├── requirements.txt         # 依赖包
└── README.md               # 说明文档
```

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置网站

在 `config/sites/` 目录下创建网站配置文件，例如 `yilzhaopin.yaml`：

```yaml
site_name: "yilzhaopin"
base_url: "https://www.ylzhaopin.com"
enabled: true
priority: 1

parsing:
  job_list_selector: "div.job-list"
  job_item_selector: "li.job-item"
  fields:
    title:
      selector: "h2.job-title a"
      type: "text"
    url:
      selector: "h2.job-title a"
      attr: "href"
      transform: "absolute_url"
    publish_date:
      selector: "span.publish-time"
      type: "date"
      format: "%Y-%m-%d"
```

### 3. 运行爬虫

```bash
# 运行所有启用的网站
python main.py --all

# 只联调前1个CSV站点（最适合先测试程序能不能跑通）
python main.py --all --site-limit 1

# 只联调前3个CSV站点
python main.py --all --site-limit 3

# 显示统计信息
python main.py --stats

# 增量模式（默认）
python main.py --all --incremental

# 强制重新爬取
python main.py --all --force

# 对本次新增公告继续抓正文、附件并生成公众号文章包
python main.py --site 重庆市人民医院 --with-details

# 先启动本地浏览器审核队列，点开原文确认后再决定哪些详情 URL 进入下一步
python main.py --site 重庆市人民医院 --with-details --select-detail-urls

# 联调详情增强时只处理前 3 条新增公告
python main.py --all --with-details --details-max-items 3

# 将某个公众号待审包里的附件上传到 idocx，并回填公众号可插入内容
python upload_wechat_attachments.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>"

# 将单个待审包推送到共享公众号草稿串
python push_wechat_review_to_official_draft.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>"

# 从一轮详情增强的 summary.json 批量推送到共享公众号草稿串
python push_wechat_review_to_official_draft.py --summary-path "/absolute/path/to/data/exports/details/<timestamp>/summary.json"

# 如需绕过 source_url 去重，可显式强制重复推送
python push_wechat_review_to_official_draft.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>" --force

# 如需继续使用旧的手动指定草稿 media_id 追加模式，也仍然支持
python push_wechat_review_to_official_draft.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>" --draft-media-id YOUR_MEDIA_ID

# 独立测试单个公告详情页
python test_detail_url_interactive.py --url "https://example.com/detail/123"

# 进入交互模式，手动粘贴详情页 URL
python test_detail_url_interactive.py
```

## PyCharm 手动测试说明

如果你平时不用命令行，直接在 PyCharm 里运行下面两个文件就够了。

### 1. 先跑基础测试

文件：`test_basic.py`

用途：检查数据库、配置加载、基础解析功能是否正常。

操作步骤：

1. 在 PyCharm 左侧项目目录里找到 `test_basic.py`
2. 右键 `test_basic.py`
3. 点击 **Run 'test_basic'**
4. 看下方 Run 窗口，出现“所有测试通过”之类的信息，说明基础环境基本没问题

适合什么时候点：
- 第一次打开项目时
- 改完配置后想先确认项目没坏
- 不确定数据库和基础模块是否正常时

### 2. 再跑主程序

文件：`main.py`

用途：真正执行爬虫主流程。

最简单操作：

1. 在 PyCharm 里打开 `main.py`
2. 右键文件
3. 点击 **Run 'main'**

但 `main.py` 需要运行参数，所以更推荐先建一个配置。

### 3. 在 PyCharm 里给 `main.py` 配置参数

操作步骤：

1. 右键 `main.py`
2. 选择 **Modify Run Configuration** 或 **Edit Configurations**
3. 找到 **Parameters** / **Script parameters**
4. 填入下面其中一组参数
5. 保存后点击右上角绿色三角运行

推荐先建这 3 个配置：

#### 配置 A：先试跑 1 个站点

```text
--all --site-limit 1
```

用途：先确认程序整体能跑，不容易一下子跑太多站点。

#### 配置 B：试跑前 3 个站点

```text
--all --site-limit 3
```

用途：做小范围联调，比直接全量运行更稳妥。

#### 配置 C：查看数据库统计

```text
--stats
```

用途：不发起爬取，只看当前数据库里已经保存了多少数据。

### 4. 运行结果怎么看

- 下方 **Run** 窗口里看到日志持续输出，说明程序正在运行
- 如果出现 `程序执行完成`，通常表示本次运行结束
- 如果出现 `ERROR`、`测试失败`、`获取页面失败`，就说明这次运行没成功
- 日志文件默认会写到 `logs/crawler.log`

### 5. 你现在主要该点哪个文件

如果你只是想手动测试：

- 先点 `test_basic.py`
- 再点 `main.py`

不要再点以前那些单独调试 jyzy120 的旧脚本，它们不是现在的正式入口。

## 配置说明

### 全局配置 (config/global.yaml)

```yaml
database:
  path: "data/jobs.db"
  echo: false

crawler:
  request_timeout: 30
  max_retries: 3
  delay_between_requests: 1.0
  user_agent: "Mozilla/5.0 ..."

waf:
  enable_detection: true
  enable_bypass: true
  stealth_mode: true
  dynamic_mode: false
```

### 网站配置字段说明

- `site_name`: 网站标识
- `base_url`: 基础URL
- `enabled`: 是否启用
- `priority`: 爬取优先级
- `parsing`: 解析规则配置
- `waf_strategy`: WAF处理策略（可选）
- `validation`: 数据验证规则（可选）

## 数据库设计

```sql
CREATE TABLE medical_jobs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT UNIQUE NOT NULL,
    publish_date DATETIME NOT NULL,
    hospital TEXT,
    location TEXT,
    source_site TEXT,
    crawl_time DATETIME,
    is_new BOOLEAN DEFAULT 1
);

CREATE TABLE crawl_status (
    site_name TEXT PRIMARY KEY,
    last_crawl_time DATETIME,
    status TEXT
);
```

## 详情文章包导出

开启 `--with-details` 后，程序会在现有“标题 + URL”链路完成后，对本次新增公告继续抓取详情正文、正文图片和附件，并输出到：

```text
data/exports/details/<timestamp>/<article_slug>/
```

每篇公告目录包含：

- `article.md`：适合继续编辑后贴到公众号后台的 Markdown 稿件
- `manifest.json`：正文抽取方式、图片、附件、转图结果和错误清单
- `source.html`：原始详情页源码
- `cleaned.html`：清洗后的正文 HTML
- `assets/inline/`：正文内图片
- `assets/attachments/`：原始附件
- `assets/renders/`：附件转出来的图片

同时会额外生成一份公众号待审包到：

```text
data/exports/wechat_review/<timestamp>/<article_slug>/
```

待审包包含：

- `article.html`：适合浏览器预览、后续整理粘贴到公众号编辑器的成稿 HTML
- `article.txt`：纯文本校对稿
- `package.json`：文章类型、图片决策、附件分类和审核标记
- `review.md`：人工快速核对摘要
- `assets/`：正文图片、岗位表转图及原始附件副本

如果需要把待审包里的附件转成公众号中可插入的 `idocx` 小程序内容，可以运行：

```bash
python upload_wechat_attachments.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>"
```

这条命令会：

- 读取 `package.json` 里的附件清单，并在待审包目录生成 `attachment_upload_staging/` 作为上传用重命名副本
- 打开 `idocx.cc` 并扫码登录；如果站点里已存在同名附件，会直接复用对应记录
- 通过网页上传控件自动上传附件，并抓取 `fid`、`sid`、小程序路径与“直达路径 HTML”
- 回填 `package.json`、更新 `article.html` 中“附件说明”为公众号可识别的小程序锚点

如果待审包已经准备好，并且 `Wechat_api/wechat-download-api` 中已配置公众号官方接口凭证，还可以直接推送到共享草稿串：

```bash
# 单篇推送，自动追加到当前批次；满 8 篇会自动切到下一份草稿
python push_wechat_review_to_official_draft.py --review-dir "/absolute/path/to/data/exports/wechat_review/<timestamp>/<article_slug>"

# 一次推送一整个 summary.json 里的 review_dir，适合手动跑完爬虫详情增强后集中入草稿箱
python push_wechat_review_to_official_draft.py --summary-path "/absolute/path/to/data/exports/details/<timestamp>/summary.json"
```
- 生成 `attachment_links.json`、`attachment_links.md`，并默认复制结果到系统剪贴板

可选详情规则写在 `config/detail_rules.yaml`，按 `site_name` 配置 `content_selectors`、`remove_selectors`、`attachment_selectors`、`prefer_readability`、`force_browser_fetch`。

## 扩展开发

### 添加新网站

1. 在 `config/sites/` 目录创建YAML配置文件
2. 定义解析规则和爬取策略
3. 测试爬取效果

### 自定义解析器

继承 `base_spider.BaseSpider` 类，重写解析方法：

```python
from spiders.base_spider import BaseSpider

class CustomSpider(BaseSpider):
    async def parse_job_detail(self, html, job_item):
        # 自定义详细页面解析逻辑
        pass
```

## 监控和日志

- 日志文件：`logs/crawler.log`
- 监控指标：通过Prometheus暴露（端口9090）
- 错误报警：失败时发送通知（需配置）

## 许可证

MIT License
