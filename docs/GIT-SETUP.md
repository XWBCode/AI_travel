# 如何在自己电脑上拿到这份代码

仓库位置（沙箱内）：`/workspace/ai-travel`，分支 `main`。

> **边界说明**：沙箱是隔离环境，我无法直接写入你本机的 `D:\`。
> 但我可以把文件打包好、放到工作区并呈现下载卡片；你只需**下载 + 解压**两步，不需要敲任何 git 命令。

---

## 方式 A：源码 zip 直接解压（推荐，最省事）

交付文件：`ai-travel-source.zip`（约 1.2M，含完整 `.git` 与 3 条提交历史，已剔除所有依赖产物）。

```powershell
# 1. 下载 ai-travel-source.zip，然后解压到 D:\projects\
#    解压后目录结构应为 D:\projects\ai-travel\README.md（不要多套一层文件夹）

# 2. 验证仓库完整
cd D:\projects\ai-travel
git log --oneline        # 应显示 3 条提交
git status               # 应为干净工作区
```

`.git` 目录整体打包后 Git 仓库依然可用（`git clone` 本质上就是复制 `.git`），**不需要** `git clone bundle`，也不会丢失提交历史或 SHA。

已在沙箱内实测：解压后 `main` 分支、3 条提交、88 个已跟踪文件、工作区干净，关键文件齐全。

## 方式 B：git bundle（适合只想要一个单文件）

交付文件：`ai-travel.bundle`（约 550K）。

```powershell
cd D:\projects
git clone ai-travel.bundle ai-travel
cd ai-travel
git log --oneline
```

---

## 关联远程仓库（可选，推荐）

拿到本地仓库后：

```bash
git remote add origin <你的仓库地址>
git push -u origin main
```

之后正常 `git push` / `git pull` 即可。如果你把仓库地址给我，我也可以在沙箱侧直接推送，你本机只需一行 `git clone <地址>`。

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

> 这只影响**之后**的提交。若要让已有的 3 条提交也显示为你，需在 push 到远程前用
> `git rebase -i --root` 配合 `--reset-author` 逐条修正，或直接在本地重做一次初始提交。
> 一旦 push 到共享仓库，就不要再改写历史。

---

## 变更记录

- 2026-11-09：改用「源码 zip 直接解压」为首选方案；纠正早期文档中「`.git` 不能直接复制」的错误说法；更新文件数与提交数。
