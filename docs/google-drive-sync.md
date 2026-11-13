# Google Drive 自动归档

首次运行补传已有课次，以后自动更新。每节课保存两个文件：`原始转录.txt` 和 `AI分析.md`，按「iCourse 课程归档 / 学期 / 课程」组织；原始字段内容保留，文件开头附课程信息。不上传音视频，不公开共享文件，也不把明文放入 GitHub artifact、代码或运行日志。

默认同步 2026-09-01 起的所有已有课次（包括已取消订阅课程），没有结束日期，避免本学期后续新课被漏掉。可用课程 ID 和日期进一步限制范围。缺少转录或分析时只上传已有内容，后续补齐；日期缺失或格式不为 ISO `YYYY-MM-DD` 时跳过并产生警告。

## 一次性授权（在自己的电脑上）

ChatGPT 中的 Drive 连接不能供 GitHub Actions 使用。此功能使用你自己的 Google OAuth 客户端，不需要提供 UIS 密码给其他服务。

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建项目并启用 **Google Drive API**。
2. 在 Google Auth Platform 配置 OAuth 同意屏幕。个人 Gmail 使用 External；若处于 Testing，先添加自己的邮箱为测试用户。长期使用前切换到 Production：Google 的 External 测试模式通常使本功能所需的 refresh token 在 7 天后过期。个人自用应用是否需要验证，以 Google 控制台显示为准。
3. 创建 **Desktop app** 类型 OAuth client，下载 JSON，仅保存在自己的电脑，不要提交到仓库。
4. 在项目目录运行：

   ```bash
   python -m pip install google-auth-oauthlib
   python scripts/authorize_drive.py /path/to/desktop-client.json
   ```

   浏览器登录你要存文件的 Google 账号并授权。脚本只请求 `drive.file`，用于管理该应用创建或用户明确授权给它的文件，不请求整个网盘权限。
5. 到仓库 **Settings → Secrets and variables → Actions → Secrets**，添加 `GOOGLE_DRIVE_OAUTH_JSON`，值为本地 `drive-oauth.secret.json` 的完整内容。该文件含长期凭据，不要发到聊天、提交到 Git 或放入运行日志。配置后妥善保管或删除本地副本。
6. 添加下表所需 Repository Variables，然后进入 **Actions → Sync Course Text to Google Drive → Run workflow**。合并代码后才会出现这个工作流。首次运行会直接解密现有数据库并同步，不重新转录、不发邮件、不需要登录 iCourse。

| Repository Variable | 默认值 | 用途 |
|---|---|---|
| `GOOGLE_DRIVE_START_DATE` | `2026-09-01` | 包含此日及以后课次 |
| `GOOGLE_DRIVE_END_DATE` | 留空 | 可选，包含此日及以前课次 |
| `GOOGLE_DRIVE_TERM` | `2026-2027 秋季学期` | 学期目录名；下一学期开始前更新日期和此名称 |
| `GOOGLE_DRIVE_COURSE_IDS` | 留空 | 留空同步日期范围内全部课程；否则逗号分隔课程 ID |
| `GOOGLE_DRIVE_PARENT_ID` | 留空 | 默认在 My Drive 根目录创建归档；指定现有目录时，OAuth 应用必须已获该目录访问权，ChatGPT 能访问不代表此应用能访问 |

建议先使用默认根目录，避免 `drive.file` 无法访问手工创建目录。更换父目录、学期目录名或 OAuth 客户端可能创建一套新归档，不会自动移动旧文件。

## 自动运行与恢复

- `iCourse Check` 或 `Single Run` 在 main 分支运行结束后自动同步；即使采集任务部分失败，也补传数据库中已经保存的内容。
- 独立每天北京时间 **20:17** 再同步一次，与原采集 cron 分离。但两者都依赖 GitHub 调度，不能替代解决 GitHub 的调度停用问题。
- 首次没有 Secret 时明确显示未配置并跳过；配置后授权失效、解密或上传失败会让同步任务变红。运行日志显示文件计数，不输出课堂正文或凭据。
- 通过 Drive 文件 ID、课次标识和内容 SHA-256 跳过未变化文件；同一课次内容变化时更新原文件。工作流并发锁避免多个同步同时创建重复文件；发现重复标识会停止并报错。
- 列举 Drive 文件支持分页。下次重跑会重查 Drive、续传缺失内容，不依赖临时 runner 的本地缓存，也不会删除 Drive 上的文件。
- 人工修改已归档文件不会回写数据库；数据库内容变化后会覆盖该文件。若要做个人批注，建议另存副本。
- 没有新运行记录时检查工作流是否被停用；同步只归档数据库里已有的数据，不会补抓因为采集停跑而尚未生成的转录。
- 此次将原采集 cron 从 19:36 改为 19:37，以便合并时重新建立调度关联。是否恢复仍需以合并后的下一次 `schedule` 运行记录为准。

本地只读预检（不使用 Google 凭据、不上传）：

```bash
python scripts/sync_drive.py --db /path/to/decrypted.db --dry-run
python -m unittest discover -s tests -v
```

Google 官方参考：[OAuth 离线访问](https://developers.google.com/identity/protocols/oauth2/web-server#offline)、[Refresh token 有效期](https://developers.google.com/identity/protocols/oauth2#expiration)、[Drive 权限范围](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)、[文件上传](https://developers.google.com/workspace/drive/api/guides/manage-uploads)。
