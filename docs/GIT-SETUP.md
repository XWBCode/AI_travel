# Git 初始化完成 —— 如何在自己电脑上拿到这份代码

仓库位置：`/workspace/ai-travel`（分支 `main`，已提交 80 个文件）

---

## 第一步：在沙箱里打成 bundle 包

Git 仓库不能直接"复制粘贴"到另一台机器还能用（`.git` 里有对象库和索引）。
最稳的方式是打成 **git bundle**——一个单文件，包含全部提交历史，拿到本机后可以完整还原。

在项目根目录执行：

```bash
cd /workspace/ai-travel
git bundle create ../ai-travel.bundle --all
```

然后把 `ai-travel.bundle` 下载到本机，例如放到 `D:\projects\`。

## 第二步：在本机还原仓库

```powershell
# Windows PowerShell / CMD
cd D:\projects
git clone ai-travel.bundle ai-travel
cd ai-travel
git log --oneline          # 应能看到首次提交
```

还原后仓库自带完整历史，`.gitignore` 已生效，不会带上 node_modules 等产物。

## 第三步：关联远程仓库（可选，推荐）

```bash
git remote add origin <你的仓库地址>
git push -u origin main
```

之后就能正常 `git push` / `git pull`。

## 第四步：还原依赖并启动

```bash
# 前端依赖
cd frontend && pnpm install && cd ..

# Java 依赖（首次会下载较多）
cd backend-java && mvn -q package && cd ..

# Python 依赖
cd backend-ai && pip install -r requirements.txt && cd ..
```

然后按 `README.md` 的「快速启动」章节操作：配 `.env`、执行 `sql/01_schema.sql`、双击 `start-all.bat`。

---

## 备选方案：不打 bundle，直接 download 文件

如果不想要提交历史，只要代码，可以直接把 `/workspace/ai-travel` 目录下载到本机，然后：

```bash
cd D:\projects\ai-travel
git init -b main
git add -A
git commit -m "chore: 初始化项目骨架"
```

效果相同，但会丢失本次提交的 SHA 和提交说明。

---

## 提交信息里的两个本地配置（可改）

当前仓库用的是占位身份，建议你在本机改成自己：

```bash
git config user.name "你的名字"
git config user.email "你的邮箱"
```

> 注意：这只影响**之后**的提交，首次提交的作者仍是 `AI Travel Dev <dev@ai-travel.local>`。
> 如果首次提交也需要正确的作者信息，请在本机用 `git commit --amend --reset-author` 修正（仅限还没 push 到远程的情况）。
