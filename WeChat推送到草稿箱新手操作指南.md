# WeChat 推送到草稿箱新手操作指南

这份文档适合第一次在当前项目里操作微信公众号草稿箱的人。你只需要照着下面的命令一步步执行，就能完成这两件事：

1. 启动本地服务
2. 把文章推送到微信公众号草稿箱

本文把常见场景合并在一个文件里：

- 场景 A：把招聘文章推送到微信公众号草稿箱
- 场景 B：把已经生成好的 `review_dir` 推送到微信公众号草稿箱

默认工作目录都是：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
```

## 当前项目状态

当前这套 `5001` 服务已经切到这个项目目录运行：

- 运行目录：`/Users/xiongtao/Documents/zhaop_automotion`
- 容器名：`wechat-download-api`
- 挂载目录：`/Users/xiongtao/Documents/zhaop_automotion -> /app`
- 当前数据库：`/Users/xiongtao/Documents/zhaop_automotion/data/rss.db`

先用这条命令确认：

```bash
docker inspect wechat-download-api --format '{{json .Mounts}}'
```

如果输出里看到的是 `/Users/xiongtao/Documents/zhaop_automotion`，说明旧目录已经不在运行链路里。

第一次从旧目录迁过来时，执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
bash scripts/migrate_legacy_wechat_runtime.sh
```

以后日常启动只需要：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
docker compose up -d
```

确认下面 3 条都正常后，旧目录就可以删除：

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

## 0. 先把整个流程理解清楚

很多人第一次都会卡在这里：

- `Fluent Reader` 里已经看到新文章了
- 但执行“导出 confirmed 招聘”的命令时，却显示没有

这通常不是程序坏了，而是这 3 件事本来就不是一个状态。

### 0.1 你以后要记住的 4 层关系

当前这套链路，建议你这样理解：

1. Docker 里的 RSS 服务在轮询公众号
2. 它把文章写进本地数据库
3. Fluent Reader 只是去读 RSS 链接，把文章显示出来
4. 只有文章在数据库里被筛成 `confirmed`，它才能进入“推公众号草稿箱”的命令结果

也就是说：

- `Fluent Reader 看到新文章`
  不等于
- `当前程序已经识别出新的 confirmed 招聘`

更不等于

- `这篇文章已经可以推到公众号草稿箱`

### 0.2 Fluent Reader 到底做了什么

Fluent Reader 的角色只是“阅读器”。

它会做的事情：

- 访问 RSS 链接
- 把 RSS 里的文章展示给你看

它不会做的事情：

- 不会把文章写回当前项目数据库
- 不会替你把文章判定成 `confirmed`
- 不会替你推送到公众号草稿箱

所以你在 Fluent Reader 里看到了新文章，只能证明：

- RSS 源里已经有文章了

不能直接证明：

- 当前程序里已经有新的 `confirmed` 招聘

### 0.3 当前程序真正看什么

当前程序判断“有没有新的招聘可以推”，看的不是 Fluent Reader，而是本地数据库里的文章状态。

最关键的字段就是：

- `review_status`

只有当它是下面这个值时，才会被“推草稿箱”的命令识别出来：

- `confirmed`

你平时最常用的判断逻辑就一句话：

- Fluent Reader 用来看“有没有新文章”
- `/api/recruitment/export?...status=confirmed&push_status=unpushed` 用来看“有没有新的招聘可以推”

### 0.4 以后每天的正确操作顺序

你以后日常使用，建议固定按下面这个顺序：

1. 先保证 Docker/RSS 服务在正常运行
2. 再确认当前程序配置允许抓正文并做招聘筛选
3. 再检查数据库里有没有新的 `confirmed` 且 `unpushed` 招聘
4. 有的话，再推送到公众号草稿箱

不要把顺序倒过来。

最容易出错的就是：

- 先在 Fluent Reader 看到了新文章
- 然后立刻去推草稿
- 但数据库里那篇文章还没进入 `confirmed`

这种情况下就会出现“明明有新文章，但命令说没有”。

## 1. 先知道你要准备什么

真正推送到微信公众号草稿箱之前，你至少要准备好下面几项：

- 当前项目代码
- Python 3.12 左右的环境
- `.env` 文件
- 公众号开放接口配置：
  - `OFFICIAL_WX_APPID`
  - `OFFICIAL_WX_APPSECRET`
- 如果你要在服务里先抓文章或查看页面，通常还需要先扫码登录公众号后台
- 如果你希望系统能把候选文章继续判成招聘，`.env` 里建议打开：
  - `RSS_FETCH_FULL_CONTENT=true`

如果你只是把已经准备好的招聘文章或 `review_dir` 推送到草稿箱，核心是 `.env` 里的官方号配置必须正确。

## 2. 第一次使用：完整准备命令

下面这一段可以直接复制执行，用来创建虚拟环境、安装依赖、准备 `.env`。

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp -n env.example .env
```

执行完后，再运行下面命令，直接用文本编辑器打开 `.env`：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
open -a TextEdit .env
```

至少确认下面这些配置已经填写：

```env
SITE_URL=http://127.0.0.1:5001
PORT=5001
HOST=0.0.0.0
RSS_FETCH_FULL_CONTENT=true

OFFICIAL_WX_APPID=改成你的公众号AppID
OFFICIAL_WX_APPSECRET=改成你的公众号AppSecret
OFFICIAL_WX_AUTHOR=白衣驿站
OFFICIAL_WX_CARD_ENABLED=true
OFFICIAL_WX_CARD_TITLE=白衣驿站
OFFICIAL_WX_CARD_SUBTITLE=关注公众号，获取更多招聘与医院资讯
OFFICIAL_WX_CARD_NOTE=以上内容由白衣驿站整理发布
OFFICIAL_WX_CARD_LINK=改成你的公众号跳转链接
OFFICIAL_WX_CARD_QR_URL=改成你的公众号二维码图片链接
```

如果你已经有现成 `.env`，可以先检查这两个最关键的值有没有配：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
grep '^OFFICIAL_WX_APPID=' .env
grep '^OFFICIAL_WX_APPSECRET=' .env
grep '^RSS_FETCH_FULL_CONTENT=' .env
```

这里强烈建议你确认 `RSS_FETCH_FULL_CONTENT=true`。

原因很简单：

- 如果是 `false`，很多文章只会进入“已抓到标题”的状态
- 但不会继续抓正文做招聘判定
- 这样你在 Fluent Reader 里能看到文章
- 但 `/api/recruitment/export?...status=confirmed...` 里可能还是看不到

## 3. 启动服务

你现在这套自动轮询链路，主流程应该走 Docker，不要再优先用旧目录里的容器。

### 3.1 Docker 启动方式

如果这是第一次把旧目录迁到当前项目，先执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
bash scripts/migrate_legacy_wechat_runtime.sh
```

如果已经迁过，日常启动直接执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
docker compose up -d
```

### 3.2 检查服务是否启动成功

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

正常情况下你会看到：

- 管理页：`http://localhost:5001/admin.html`
- 登录页：`http://localhost:5001/login.html`
- 接口文档：`http://localhost:5001/api/docs`

### 3.3 只在本地调试代码时，才直接运行 Python

如果你是在本机直接调试，不走 Docker，再用下面这组：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python app.py
```

### 3.4 如果需要扫码登录

如果你后面要通过本地服务管理公众号、搜索公众号、抓公众号文章，通常要先登录。

直接在浏览器打开：

```text
http://127.0.0.1:5001/login.html
```

用公众号管理员微信扫码即可。

## 4. 以后每天怎么操作

这一节最重要。你现在的使用场景是：

- `rss docker` 会自动轮询公众号
- 你想确认有没有新的招聘文章进入程序
- 有的话，再把它推到公众号草稿箱

所以你以后每天的标准流程，不是“先去 Fluent Reader 手动拉取”，而是下面这条链路：

1. 先确认服务和 RSS 轮询器正常
2. 让程序自动轮询，或者在你想马上更新时手动触发一次轮询
3. 查询数据库里有没有新的 `confirmed + unpushed` 招聘文章
4. 有的话，推送到共享草稿串
5. 再查共享草稿串状态，确认是否成功

你可以把它理解成：

- `Fluent Reader` 负责“阅读 RSS”
- 当前项目接口负责“判断招聘、决定能不能推草稿箱”

### 4.0 Docker 自动轮询时，我到底要不要手动拉取

通常不用。

如果你的 `rss docker` 本来就在自动轮询，那么平时正确做法是：

- 先等它自己轮询
- 直接查 `/api/recruitment/export?...status=confirmed&push_status=unpushed`
- 有结果就推草稿箱

只有在下面两种情况，你才需要“手动拉一次”：

1. 你刚订阅完一个公众号，想立刻看到结果
2. 你在 Fluent Reader 里已经看到新文章，但程序这边还没来得及跑到那批订阅

这时你才去调用：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

如果它提示“当前没有到期订阅”，说明不是接口坏了，而是当前订阅还没到调度时间。

### 4.1 第一步：先确认服务还活着

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
```

你至少要看到：

- 服务正常响应
- RSS 轮询器是运行中的

### 4.2 第二步：确认这个公众号已经在当前服务里订阅过

这一步通常只需要第一次做，后面不用每天做。

先看当前服务已经订阅了哪些公众号：

```bash
curl "http://127.0.0.1:5001/api/rss/subscriptions"
```

如果你要补订一个公众号，标准流程是：

1. 先搜索公众号，拿到 `fakeid`
2. 再添加订阅

搜索公众号：

```bash
curl "http://127.0.0.1:5001/api/public/searchbiz?query=这里换成公众号名称"
```

添加订阅：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/subscribe" \
  -H "Content-Type: application/json" \
  -d '{"fakeid":"这里替换成fakeid","nickname":"这里替换成公众号名"}'
```

### 4.3 第三步：确认你看的 Fluent Reader 确实是这套服务的 RSS

先看当前服务的聚合 RSS：

```bash
curl "http://127.0.0.1:5001/api/rss/all"
```

你在 Fluent Reader 里看到的新文章，最好就是来自这套服务暴露出来的 RSS 地址。

如果 Fluent Reader 订阅的是别的 RSS 服务、别的 Docker 实例、别的端口，那它看到的新文章并不会自动进入当前这个项目的数据库判断结果里。

最推荐你在 Fluent Reader 里订阅这个聚合地址：

- `http://127.0.0.1:5001/api/rss/all`

这样你看到的文章，和当前程序判断招聘时使用的数据源，就是同一套服务。

### 4.4 第四步：确认“有没有新文章”和“有没有可推招聘”是两次检查

先看是否有新文章：

```bash
curl "http://127.0.0.1:5001/api/rss/all"
```

再看是否有新的 confirmed 招聘可以推：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

记住：

- 第一条命令回答的是“有没有新文章”
- 第二条命令回答的是“有没有新的招聘可以推”

这两个问题不是一回事。

### 4.5 第五步：Docker 自动轮询场景下，我平时具体怎么查“有没有新招聘”

你以后最常用、最实用的，其实就是下面这条命令：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

你只需要看两个地方：

- `count`
- `data`

怎么理解：

- `count > 0`
  说明数据库里已经有新的招聘文章，并且还没推送过，现在就可以推草稿箱
- `count = 0`
  说明当前没有“已确认且未推送”的招聘文章，不要直接点推草稿箱

如果你想看更完整内容，用这条：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=full"
```

### 4.6 第六步：如果 Fluent Reader 有新文章，但导出里没有

这是你现在最常见的情况。

通常按下面顺序处理：

1. 确认 `.env` 里是 `RSS_FETCH_FULL_CONTENT=true`
2. 重启服务
3. 手动触发一次轮询
4. 再查 `confirmed + unpushed`

直接照抄下面这一组：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
grep '^RSS_FETCH_FULL_CONTENT=' .env
source .venv/bin/activate
python app.py
```

服务启动后，再开另一个终端执行：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

### 4.7 第七步：如果手动轮询返回“当前没有到期订阅”

这是另一个高频情况。

因为这个接口只会处理“当前到期的订阅”，不是你点一下就强制全量重抓。

如果你就是想让它马上再跑一次，可以执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
sqlite3 data/rss.db "UPDATE subscriptions SET next_poll_at=0;"
```

然后再手动触发轮询：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

再重新检查有没有新的招聘可推：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

### 4.8 第八步：确认有可推招聘后，再推到草稿箱

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

推完再看草稿串状态：

```bash
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

### 4.9 第九步：你以后每天就照这个最短流程走

如果你已经订阅过公众号，而且 `rss docker` 会自动轮询，那么你每天只需要做这 3 步：

1. 查有没有新的 confirmed 招聘

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

2. 如果有，就推送到共享草稿串

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

3. 再查共享草稿串状态

```bash
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

只有在“Fluent Reader 已经看到文章，但程序还没识别出来”时，才插入这一步：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

## 5. 最短跑通路径

如果你只想先验证“能不能推到草稿箱”，优先用下面这条最短路径：

1. 配好 `.env` 里的 `OFFICIAL_WX_APPID` 和 `OFFICIAL_WX_APPSECRET`
2. 确认 `.env` 里的 `RSS_FETCH_FULL_CONTENT=true`
3. 启动服务
4. 执行一个推送命令

最短命令如下：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

这条命令的含义是：

- 从数据库里找最新一篇还没进入共享草稿串的 `confirmed` 招聘文章
- 自动推送到微信公众号共享草稿串
- 如果当前草稿没满 8 篇，就继续追加
- 如果当前草稿满了，就自动新建下一份草稿

## 5.1 推送前先确认有没有新的招聘

如果你想先确认“现在数据库里有没有新的招聘可以推”，最实用的就是查：

- 已经被筛成 `confirmed`
- 还没有推送过

直接执行这条命令：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

这条命令的意思是：

- `status=confirmed`
  只看已经确认是招聘的文章
- `push_status=unpushed`
  只看还没推送到草稿箱的文章
- `profile=title_url`
  只返回标题和原文链接，最适合快速确认
- `limit=20`
  最多看最近 20 篇

怎么看结果：

- 如果返回里 `count` 大于 `0`，说明有新的招聘可以推
- 如果 `count` 等于 `0`，说明当前没有“已确认且未推送”的招聘文章

如果你想看更完整的信息，而不只是标题和链接，用这条：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=full"
```

如果你想把结果导出成 CSV 文件方便打开看，用这条：

```bash
curl -OJ "http://127.0.0.1:5001/api/recruitment/export?format=csv&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

## 5.2 一眼看懂为什么 Fluent Reader 有而这里没有

如果你遇到下面这种情况：

- Fluent Reader 已经看到新文章
- 但导出 `confirmed` 招聘时还是 `count=0`

优先按下面理解，不要先怀疑草稿接口：

1. RSS 有新文章
2. 文章已经进入数据库
3. 但文章还没被判成 `confirmed`
4. 所以还不能进入推草稿命令

这时最常见原因是：

- `RSS_FETCH_FULL_CONTENT=false`
- 或者轮询还没重新跑到这批订阅
- 或者这篇文章压根没命中招聘规则

你可以直接用下面命令看最近文章在数据库里的状态：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
sqlite3 -header -column data/rss.db "SELECT datetime(publish_time,'unixepoch','localtime') AS publish_time, substr(title,1,36) AS title, review_status, filter_stage FROM articles ORDER BY publish_time DESC LIMIT 20;"
```

怎么看：

- `review_status=confirmed`
  说明已经是可推招聘
- `review_status=manual_review`
  说明命中了部分条件，但还没最终确认
- `review_status=rejected`
  说明被排除了
- `review_status` 为空
  说明只是抓到了文章，但还没进入最终招聘判断结果

## 5.3 你这个场景下，一条完整的实际操作示例

假设你的 `rss docker` 已经在自动轮询，你今天想做的事情是：

- 看看有没有新的招聘文章
- 如果有，就推送到公众号草稿箱

那你直接照下面执行：

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果第三条结果里 `count > 0`，继续执行：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

如果第三条结果里 `count = 0`，但你在 Fluent Reader 已经看到了新文章，再补执行：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果还是没有，再查数据库里的文章状态：

```bash
sqlite3 -header -column data/rss.db "SELECT datetime(publish_time,'unixepoch','localtime') AS publish_time, substr(title,1,50) AS title, review_status, filter_stage FROM articles ORDER BY publish_time DESC LIMIT 30;"
```

## 5.4 当前 5001 容器专用终端清单

这一节是给你现在这台正在运行的 Docker 服务专门准备的。

你当前实际在用的是：

- 接口地址：`http://127.0.0.1:5001`
- 实际数据库：`/Users/xiongtao/Documents/zhaop_automotion/data/rss.db`
- 实际运行目录：`/Users/xiongtao/Documents/zhaop_automotion`

现在 `5001` 端口上的容器，就是在读当前仓库这份库。

### 5.4.1 先确认服务正常

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
docker inspect wechat-download-api --format '{{json .Mounts}}'
```

### 5.4.2 查有没有新的招聘可推

这是你以后最常用的一条：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果你想看完整字段：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=full"
```

### 5.4.3 直接看数据库里的未推送招聘

```bash
sqlite3 -header -column "/Users/xiongtao/Documents/zhaop_automotion/data/rss.db" "
SELECT
  datetime(a.publish_time,'unixepoch','localtime') AS publish_time,
  substr(a.title,1,60) AS title,
  a.review_status,
  coalesce(d.draft_status,'') AS draft_status
FROM articles a
LEFT JOIN official_draft_sync d ON d.source_link=a.link
WHERE a.review_status='confirmed' AND d.source_link IS NULL
ORDER BY a.publish_time DESC
LIMIT 20;
"
```

### 5.4.4 如果 Fluent Reader 有新文章，但接口还没出来

先手动触发一次轮询：

```bash
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

再查一次：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果它提示“当前没有到期订阅”，就把订阅强制改成立刻到期：

```bash
sqlite3 "/Users/xiongtao/Documents/zhaop_automotion/data/rss.db" "UPDATE subscriptions SET next_poll_at=0;"
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

### 5.4.5 推送最新一篇招聘到共享草稿串

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

推完看状态：

```bash
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

### 5.4.6 推指定那篇招聘

把 `source_url` 换成你要推的那篇链接：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push?source_url=https://mp.weixin.qq.com/s/773q7dBGOFdNKOgZEY72QQ"
```

### 5.4.7 查最近文章状态

如果你想看“为什么它没进招聘”，用这条：

```bash
sqlite3 -header -column "/Users/xiongtao/Documents/zhaop_automotion/data/rss.db" "
SELECT
  datetime(publish_time,'unixepoch','localtime') AS publish_time,
  substr(title,1,70) AS title,
  review_status,
  filter_stage
FROM articles
ORDER BY publish_time DESC
LIMIT 30;
"
```

### 5.4.8 查标题里像招聘、但还没进 confirmed 的文章

```bash
sqlite3 -header -column "/Users/xiongtao/Documents/zhaop_automotion/data/rss.db" "
SELECT
  datetime(publish_time,'unixepoch','localtime') AS publish_time,
  substr(title,1,70) AS title,
  review_status,
  filter_stage
FROM articles
WHERE title LIKE '%招聘%'
   OR title LIKE '%招募%'
   OR title LIKE '%诚聘%'
   OR title LIKE '%聘用%'
   OR title LIKE '%人才引进%'
   OR title LIKE '%英才%'
ORDER BY publish_time DESC
LIMIT 30;
"
```

### 5.4.9 你每天就跑这三条就够了

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

## 6. 场景 A：推送招聘文章到微信公众号草稿箱

这一部分适合你已经有招聘文章数据，并且文章已经进入当前项目数据库的情况。

### 6.1 推送最新一篇招聘文章到共享草稿串

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

### 6.2 推送指定文章 URL 到共享草稿串

把下面命令里的 `source_url=` 改成你要推送的招聘原文链接：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push?source_url=https://example.com/recruit/123"
```

### 6.3 如果同一个 URL 你就是要重复推送

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push?source_url=https://example.com/recruit/123&force=true"
```

### 6.4 查看当前共享草稿串状态

```bash
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

这个接口最值得看这几个字段：

- `current_media_id`：当前草稿的 media_id
- `current_article_count`：当前草稿里已经有几篇文章
- `batch_index`：当前是第几批草稿
- `has_active_draft`：当前是否存在可继续追加的草稿

### 6.5 不走共享串，单独创建一篇草稿

如果你不想用共享草稿串，而是想单独创建一个草稿，可以用旧接口：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/push"
```

指定某个招聘原文链接时这样写：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/push?source_url=https://example.com/recruit/123"
```

### 6.6 向一个已有草稿继续追加文章

如果你手里已经有一个现成的 `draft_media_id`，可以直接把新文章追加进去：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/append?draft_media_id=这里替换成现有media_id&source_url=https://example.com/recruit/123"
```

## 7. 场景 B：把 review_dir 推送到微信公众号草稿箱

这一部分适合你已经通过 crawler 或 detail pipeline 生成了 `wechat_review/review_dir`，现在只想把它推到公众号草稿箱。

当前项目已经准备好了现成脚本：

- 根目录入口：`push_wechat_review_to_official_draft.py`
- 实际脚本：`scripts/push_wechat_review_to_official_draft.py`

你平时直接调用根目录入口就行。

### 7.1 推送单个 review_dir 到共享草稿串

把下面路径换成你自己的 `review_dir` 绝对路径：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --review-dir "/绝对路径/到/review_dir"
```

### 7.2 如果要允许重复推送

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --review-dir "/绝对路径/到/review_dir" --force
```

### 7.3 根据 summary.json 批量推送多个 review_dir

如果你手里有一轮详情处理后的 `summary.json`，可以让脚本把里面的所有 `review_dir` 按顺序推送：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --summary-path "/绝对路径/到/summary.json"
```

### 7.4 向已有草稿追加某个 review_dir

如果你已经知道某个草稿的 `draft_media_id`，可以让脚本直接追加：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --review-dir "/绝对路径/到/review_dir" --draft-media-id "这里替换成现有media_id"
```

注意：

- `--summary-path` 模式不能和 `--draft-media-id` 一起用
- `review_dir` 必须是真实存在的绝对路径
- `review_dir` 里面通常至少要有 `package.json`

## 8. 一条命令检查草稿箱推送前置条件

如果你想先快速检查环境是否大致就绪，可以复制下面这一段：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python - <<'PY'
from pathlib import Path
from dotenv import dotenv_values

env_path = Path(".env")
print(f".env exists: {env_path.exists()}")
if env_path.exists():
    env = dotenv_values(env_path)
    for key in [
        "OFFICIAL_WX_APPID",
        "OFFICIAL_WX_APPSECRET",
        "SITE_URL",
        "PORT",
        "RSS_FETCH_FULL_CONTENT",
    ]:
        value = (env.get(key) or "").strip()
        if value:
            print(f"{key}: OK")
        else:
            print(f"{key}: MISSING")
PY
```

## 9. 常见报错和处理办法

### 9.1 报错：`OFFICIAL_WX_APPID / OFFICIAL_WX_APPSECRET 未配置`

说明你的 `.env` 里还没配公众号开放接口信息。

处理步骤：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
open -a TextEdit .env
```

然后补上：

```env
OFFICIAL_WX_APPID=你的真实AppID
OFFICIAL_WX_APPSECRET=你的真实AppSecret
```

保存后重启服务：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python app.py
```

### 9.2 报错：`Failed to connect` 或 `Connection refused`

说明本地服务还没启动。

直接重新启动服务：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python app.py
```

### 9.3 Fluent Reader 有新文章，但导出命令还是没有

这是最容易误解的情况。

原因通常是：

- 文章只是进入了 RSS
- 但还没有进入 `confirmed`

优先执行下面这一组：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
grep '^RSS_FETCH_FULL_CONTENT=' .env
sqlite3 data/rss.db "UPDATE subscriptions SET next_poll_at=0;"
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果第一条看到的是：

```text
RSS_FETCH_FULL_CONTENT=false
```

先改成：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
sed -i '' 's/^RSS_FETCH_FULL_CONTENT=.*/RSS_FETCH_FULL_CONTENT=true/' .env
```

然后重启服务，再重新执行上面的轮询和检查。

### 9.4 报错：`该文章已经推送过草稿箱，默认不再重复推送`

说明同一个 `source_url` 之前已经推过了。

如果你确定要再次推送，就加 `force=true`：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push?source_url=https://example.com/recruit/123&force=true"
```

或者脚本模式下加：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --review-dir "/绝对路径/到/review_dir" --force
```

### 9.5 报错：`review_dir 不存在`

说明你传进去的路径不对。

先用下面命令确认路径真实存在：

```bash
ls -la "/绝对路径/到/review_dir"
```

如果这个目录不存在，先去找到真实的 `review_dir` 再重新执行。

### 9.6 报错：未找到指定招聘文章

说明你传入的 `source_url` 没在当前数据库里。

你可以先导出最近的招聘文章标题和链接确认一下：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=all&limit=20&profile=title_url"
```

找到正确的 `source_url` 后再重新推送。

## 10. 建议你最常用的命令清单

### 10.1 启动服务

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python app.py
```

### 10.2 看当前服务是否正常

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
```

### 10.3 看当前服务是否有订阅

```bash
curl "http://127.0.0.1:5001/api/rss/subscriptions"
```

### 10.4 看当前有没有新的 confirmed 招聘

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

### 10.5 强制让订阅马上重新轮询一次

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
sqlite3 data/rss.db "UPDATE subscriptions SET next_poll_at=0;"
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
```

### 10.6 推送最新一篇招聘文章到共享草稿串

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

### 10.7 查看共享草稿串状态

```bash
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

### 10.8 推送单个 review_dir

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --review-dir "/绝对路径/到/review_dir"
```

### 10.9 批量推送 summary.json 里的 review_dir

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python push_wechat_review_to_official_draft.py --summary-path "/绝对路径/到/summary.json"
```

## 11. 日常最推荐的固定流程

如果你以后每天就是“看有没有新招聘，然后推草稿”，最推荐固定照下面执行：

### 11.1 先确认服务活着

```bash
curl "http://127.0.0.1:5001/api/health"
curl "http://127.0.0.1:5001/api/rss/status"
```

### 11.2 再看有没有新的 confirmed 招聘

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

### 11.3 如果这里没有，但 Fluent Reader 明明有新文章

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
sqlite3 data/rss.db "UPDATE subscriptions SET next_poll_at=0;"
curl -X POST "http://127.0.0.1:5001/api/rss/poll"
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

### 11.4 如果这里有数据，再推送到草稿箱

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
curl "http://127.0.0.1:5001/api/recruitment/official-draft/series/status"
```

## 12. 你可以从这里开始

如果你现在只是想先成功推一次，最推荐你直接照这个顺序执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp -n env.example .env
open -a TextEdit .env
```

把下面 3 个值确认好：

- `OFFICIAL_WX_APPID`
- `OFFICIAL_WX_APPSECRET`
- `RSS_FETCH_FULL_CONTENT=true`

然后再执行：

```bash
cd /Users/xiongtao/Documents/zhaop_automotion
source .venv/bin/activate
python app.py
```

保持服务运行，再开另一个终端执行：

```bash
curl "http://127.0.0.1:5001/api/recruitment/export?format=json&status=confirmed&push_status=unpushed&limit=20&profile=title_url"
```

如果这里已经能看到可推招聘，再执行：

```bash
curl -X POST "http://127.0.0.1:5001/api/recruitment/official-draft/series/push"
```

如果返回成功 JSON，说明这条“推送到微信公众号草稿箱”的链路已经跑通了。
