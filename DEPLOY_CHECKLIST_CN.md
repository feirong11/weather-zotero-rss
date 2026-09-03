# 部署检查清单

- [ ] 创建 GitHub 仓库（建议 `weather-zotero-rss`）
- [ ] 上传 ZIP 解压后的全部文件
- [ ] 确认 `.github/workflows/update-rss.yml` 存在
- [ ] Settings → Pages → Source = GitHub Actions
- [ ] Actions → Update Zotero RSS → Run workflow
- [ ] 打开 Pages 首页，确认 3 个频道都有链接
- [ ] 打开 `status.json`，检查是否有期刊报错
- [ ] 在 Zotero 添加：
  - [ ] `tropical-cyclone.xml`
  - [ ] `machine-learning.xml`
  - [ ] `low-altitude-economy.xml`
- [ ] 以后无需在本机运行 Python
