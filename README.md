# 招聘抓取与公众号草稿箱一体化项目

这个仓库已经合并为一个根工作区，目标是完成以下整条链路：

1. 抓取医院招聘公告
2. 生成详情和公众号文章包
3. 推送到公众号草稿箱

## 主入口

- `app.py`：FastAPI 服务入口
- `main.py`：招聘爬虫入口

## 主要目录

- `crawler/`：爬虫核心、模型、Spider
- `wechat_service/`：微信服务路由和草稿箱推送逻辑
- `scripts/`：批处理和调试脚本
- `shared/`：共享路径和环境加载
- `config/`：爬虫配置和规则
- `static/`：FastAPI 静态页面
- `assets/`：说明图片和二维码资源
- `data/`：运行数据库
- `logs/`：运行日志

## VSCode 运行

根目录已提供 `.vscode/launch.json`：

- `FastAPI 服务`
- `招聘爬虫`

## 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

环境变量统一使用根目录 `.env`，模板见 `env.example`。
