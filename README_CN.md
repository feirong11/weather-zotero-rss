# GitHub + Zotero 自动期刊监控

这套仓库已经按用户提供的 **22 本期刊** 和 **3 个频道**配置完成。

## 频道

### 1. Tropical Cyclone
- tropical cyclone
- typhoon
- tropical storm
- tropical convection
- hurricane

### 2. Machine Learning
- machine learning
- AI model
- deep learning
- neutral network
- artificial intelligence

### 3. Low-altitude Economy
- low-altitude meteorology
- low-altitude weather
- low-altitude meteorological

匹配字段：**Title OR Abstract**，大小写不敏感。

---

## 为什么 GitHub 版不直接依赖 22 家出版社的 RSS？

最终给 Zotero 的输出仍然是标准 RSS。

但是上游监控默认采用：

**期刊 ISSN → Crossref 最近论文 → OpenAlex / 出版商页面补摘要 → 关键词筛选 → RSS**

原因是 AMS、Wiley/AGU、J-STAGE、Springer、Elsevier、Nature、Science 等平台的 RSS URL/格式和反爬策略不同。
对于完全无人值守的 GitHub Actions，使用 ISSN + DOI 元数据源通常比同时维护 22 个出版社 Feed URL 稳定。

脚本按 DOI/标题哈希去重，并保存 `state/articles.json`。

---

# 一次性部署流程

## A. 创建 GitHub 仓库

1. 登录 GitHub。
2. 点击右上角 `+` → `New repository`。
3. Repository name 建议：
   `weather-zotero-rss`
4. 建议选择 `Public`。
5. 点击 `Create repository`。

> GitHub Pages 的可用性取决于你的 GitHub 计划和仓库可见性。使用 Public 最简单。

---

## B. 上传整个仓库

解压本 ZIP 后，**上传文件夹里面的内容**，不要再多套一层目录。

GitHub 仓库根目录最终应看到：

```text
.github/
  workflows/
    update-rss.yml
channels.yaml
journals.yaml
settings.yaml
requirements.txt
rss_filter.py
state/
  articles.json
docs/
README_CN.md
```

可以直接在 GitHub 网页：

`Add file` → `Upload files`

把全部文件拖进去，然后 Commit。

注意 `.github` 是以点开头的目录；确保它也上传成功。

---

## C. 开启 GitHub Pages

进入仓库：

`Settings` → `Pages`

在 **Build and deployment** 中，把 Source 设为：

`GitHub Actions`

无需选择 main/docs 分支。

---

## D. 第一次手动运行

进入：

`Actions` → `Update Zotero RSS`

点击：

`Run workflow` → `Run workflow`

等待运行结束。

即使某一家期刊临时请求失败，工作流仍会继续生成其他期刊的结果。
具体错误会写入公开页面的 `status.json`。

---

## E. 找到你的 Pages 地址

第一次部署成功后：

`Settings` → `Pages`

会显示站点地址。

普通项目仓库通常类似：

```text
https://YOUR_GITHUB_USERNAME.github.io/weather-zotero-rss/
```

打开后会看到三个 RSS 频道链接。

脚本在 GitHub Actions 中会根据仓库名自动推断 Pages URL，所以你不需要手动修改用户名。

---

# Zotero 订阅地址

如果仓库名是 `weather-zotero-rss`：

### Tropical Cyclone

```text
https://YOUR_GITHUB_USERNAME.github.io/weather-zotero-rss/tropical-cyclone.xml
```

### Machine Learning

```text
https://YOUR_GITHUB_USERNAME.github.io/weather-zotero-rss/machine-learning.xml
```

### Low-altitude Economy

```text
https://YOUR_GITHUB_USERNAME.github.io/weather-zotero-rss/low-altitude-economy.xml
```

### 全部命中论文

```text
https://YOUR_GITHUB_USERNAME.github.io/weather-zotero-rss/all-matched.xml
```

---

# 在 Zotero 中添加

Zotero 左侧 Feeds：

`New Feed / 新建订阅源`
→ `From URL / 从 URL`
→ 粘贴其中一个 `.xml` URL

建议分别添加三个频道。

---

# 以后是否还需要运行 Python？

**不需要在你的电脑上运行。**

GitHub Actions 默认：

```text
17 */6 * * *
```

也就是每 6 小时运行一次（GitHub cron 使用 UTC）。

链路是：

```text
GitHub Actions
    ↓
Crossref 检查 22 本期刊近期文章
    ↓
必要时补 Abstract
    ↓
Title / Abstract 关键词判断
    ↓
更新 XML
    ↓
GitHub Pages
    ↓
Zotero 刷新 Feed
```

你的电脑关机不影响 GitHub 更新。

---

# 修改关键词

只编辑：

`channels.yaml`

例如 Machine Learning 想增加：

```yaml
keywords:
  - "machine learning"
  - "AI model"
  - "deep learning"
```

提交后，可以等下一次自动运行，或者：

`Actions` → `Update Zotero RSS` → `Run workflow`

---

# 暂停一本期刊

打开 `journals.yaml`：

```yaml
enabled: true
```

改成：

```yaml
enabled: false
```

---

# 期刊说明

上传的列表最后一项写的是：

`Scientific Report`

本配置保留这个 `name`，但监控目标按 Nature Portfolio 的期刊：

`Scientific Reports`（ISSN 2045-2322）

处理。

如果你原本指的不是 Scientific Reports，请修改 `journals.yaml`。

---

# 摘要缺失与漏检

过滤要求是 Title OR Abstract。

程序的顺序：

1. Crossref 标题与摘要；
2. 摘要不足时查询 OpenAlex；
3. 仍不足时尝试文章落地页 `<meta>` 摘要；
4. 再执行关键词匹配。

某些出版商页面可能阻止自动访问，且部分 DOI 元数据没有摘要。
这意味着极少数“标题没有关键词、但真实摘要有关键词且所有元数据源都拿不到摘要”的论文可能漏掉。

站点的：

`status.json`

会给出 `missing_or_short_abstract_count`，便于检查。

---

# 文件说明

```text
rss_filter.py
  主程序

journals.yaml
  22 本期刊和 ISSN

channels.yaml
  3 个频道与关键词

settings.yaml
  抓取范围、历史长度、摘要补全等设置

.github/workflows/update-rss.yml
  每 6 小时自动运行并部署 GitHub Pages

state/articles.json
  DOI/文章状态缓存

docs/
  生成的 GitHub Pages + RSS
```

---

# 本地测试（可选）

这不是日常运行所必需。

```bash
python -m pip install -r requirements.txt
python rss_filter.py
```

然后查看：

```text
docs/index.html
docs/tropical-cyclone.xml
docs/machine-learning.xml
docs/low-altitude-economy.xml
docs/all-matched.xml
```

---

# 常用调整

## 每 12 小时更新

`.github/workflows/update-rss.yml`

把：

```yaml
- cron: "17 */6 * * *"
```

改为：

```yaml
- cron: "17 */12 * * *"
```

## RSS 保留最近一年

`settings.yaml`：

```yaml
feed_history_days: 365
```

## 只检查题目

`channels.yaml`：

```yaml
fields: ["title"]
```

---

# 重要说明

- GitHub 定时任务不是实时任务，可能相对 cron 时间有短暂延迟。
- GitHub Actions 的 `schedule` 只在默认分支上的 workflow 生效。
- 公共仓库长期完全没有活动时，GitHub 对 scheduled workflows 有自动禁用机制；本仓库仅在出现新文章、摘要补全或状态实际变化时提交 `state/articles.json`。你监控的 22 本活跃期刊通常会持续产生这类变化。
- RSS GUID 使用 DOI 优先，因此 Zotero 不应把同一 DOI 当作全新的条目反复显示。
