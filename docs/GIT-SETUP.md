# 如何在自己电脑上拿到这份代码

仓库位置（沙箱内）：`/workspace/ai-travel`，分支 `main`。
远程仓库：已绑定 `https://github.com/XWBCode/AI_travel.git`（`origin`）。

> **边界说明**：沙箱是隔离环境，我无法直接写入你本机的 `D:\`。
> 但我可以把文件打包好、放到工作区并呈现下载卡片；你只需**下载 + 解压**两步。

---

## 方式 A：源码 zip 直接解压（推荐，最省事）

交付文件：`ai-travel-source.zip`（约 1.3M，含完整 `.git` 与全部提交历史，已剔除所有依赖产物）。

```powershell
# 1. 下载 ai-travel-source.zip，解压到 D:\projects\
#    解压后应为 D:\projects\ai-travel\README.md（不要多套一层文件夹）

# 2. 验证仓库完整
cd D:\projects\ai-travel
git log --oneline        # 应能看到 Initial commit 与各阶段提交
git remote -v            # 应显示已绑定的 origin
git status               # 应为干净工作区
```

`.git` 目录整体打包后 Git 仓库依然可用（`git clone` 本质就是复制 `.git`），**不需要** `git clone bundle`，也不会丢失提交历史或 SHA。

已在沙箱内实测：解压后 `main` 分支、88 个已跟踪文件、工作区干净、`origin` 已配置。

## 方式 B：git bundle（适合只想要一个单文件）

交付文件：`ai-travel.bundle`（约 554K）。

```powershell
cd D:\projects
git clone ai-travel.bundle ai-travel
cd ai-travel
git remote add origin https://github.com/XWBCode/AI_travel.git
```

> 从 bundle 克隆出来的仓库**不带** remote，需按上面补一行；方式 A 的 zip 已内置 remote。

## 方式 C：直接从 GitHub 克隆（推送成功后最推荐）

```powershell
cd D:\projects
git clone https://github.com/XWBCode/AI_travel.git
```

代码推送到远程仓库后，这是最干净的方式，后续 `git push` / `git pull` 直接可用。

---

## 远程仓库与推送

zip 内的仓库**已经绑定** `origin`，不要再执行 `git remote add`（会报 already exists）。

```bash
git remote -v                          # 查看
git push -u origin main                # 本机配置好 GitHub 凭据后推送
git remote set-url origin <新地址>      # 需要改地址时用这个
```

GitHub 的 Initial commit 已合并进本地历史，因此 push 是 fast-forward，**不会冲突、不需要强推**。

## 还原依赖并启动

```bash
# 前端依赖
cd frontend && pnpm install && cd ..

# Java 依赖（首次会下载较多）
cd backend-java && mvn -q package && cd ..

# Python 依赖
cd backend-ai && pip install -r requirements.txt && cd ..
```

然后按 `README.md` 的「快速启动」：配 `.env`（复制 `.env.example`）、执行 `sql/01_schema.sql`、双击 `start-all.bat`。

---

## 提交作者是占位身份（可改）

```bash
git config user.name "你的名字"
git config user.email "你的邮箱"
```

> 这只影响**之后**的提交。若要让已有提交也显示为你，需在 push 到远程**之前**用
> `git rebase -i --root` 配合 `--reset-author` 逐条修正。一旦 push 到共享仓库，就不要再改写历史。

---

## 变更记录

- 2026-11-09：改用「源码 zip 直接解压」为首选方案；纠正早期文档中「`.git` 不能直接复制」的错误说法。
- 2026-11-09：绑定远程仓库 `XWBCode/AI_travel`；合并其 Initial commit（README / .gitignore 冲突已解决）；补充方式 C（从 GitHub 克隆）。
