# 非 Git 同步文件核对清单

`.gitignore` 会让一部分文件不进入 GitHub。它们需要在首次部署、迁移服务器、恢复环境或更新数据时单独核对。

## 必须核对的数据文件

这些源数据通常体积较大，不建议提交 GitHub：

```text
data/source/questions.jsonl
data/source/documents.jsonl
data/source/chunks.jsonl
data/source/chunks_v2.jsonl
data/source/chunks_v3.jsonl
```

核对命令：

```bash
ls -lh data/source/
```

如果新增 chunk 版本，确认文件中每行都有一致的 `chunk_version` 字段。

## 必须保护的运行数据

这些文件记录真实标注状态，不应被本地空文件或旧文件覆盖：

```text
data/annotations.sqlite3
evaluation/annotations/questions_annotated_v1.jsonl
backups/
```

核对命令：

```bash
ls -lh data/
ls -lh evaluation/annotations/
ls -lh backups/
```

正式标注开始后，`data/annotations.sqlite3` 是最重要的文件。

## 不需要同步的临时文件

这些通常可以忽略或删除：

```text
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.pyc
*.log
logs/
tmp/
temp/
```

## 首次部署核对

第一次整包上传后，在服务器项目目录执行：

```bash
cd /workspace/fintrace-labeling
ls -lh
ls -lh data/source/
```

至少应看到：

```text
app/
data/source/questions.jsonl
data/source/documents.jsonl
data/source/chunks_v2.jsonl
requirements.txt
README.md
```

然后执行：

```bash
conda activate FinTrace-Labeling
pip install -r requirements.txt
python -m app.import_documents --file data/source/documents.jsonl
python -m app.import_chunks --file data/source/chunks_v2.jsonl --activate
```

## 后续 Git 更新前核对

服务器更新代码前，先备份数据库：

```bash
cd /workspace/fintrace-labeling
mkdir -p backups
cp data/annotations.sqlite3 backups/annotations.$(date +%Y%m%d-%H%M%S).sqlite3
```

再更新代码：

```bash
git pull
systemctl restart fintrace-labeling
```

## 迁移服务器核对

迁移到新服务器时，Git 只能恢复代码，不能恢复被 `.gitignore` 忽略的数据。

需要额外复制：

```text
data/source/
data/annotations.sqlite3
evaluation/annotations/
backups/
```

示例：

```bash
scp -r root@121.43.58.18:/workspace/fintrace-labeling/data/source ./data/
scp root@121.43.58.18:/workspace/fintrace-labeling/data/annotations.sqlite3 ./data/
scp -r root@121.43.58.18:/workspace/fintrace-labeling/evaluation/annotations ./evaluation/
```

## 当前 .gitignore 规则对应清单

以下内容不会被 Git 同步：

```text
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
build/
dist/
*.egg-info/
.venv/
venv/
env/
.env
.env.*
.idea/
.vscode/
.DS_Store
Thumbs.db
data/*.sqlite3
data/*.sqlite3-*
data/*.db
data/*.db-*
data/*.before-reset-*.sqlite3
backups/
evaluation/annotations/*.jsonl
evaluation/annotations/*.json
chunks*.jsonl
documents*.jsonl
data/source/*.jsonl
*.log
logs/
tmp/
temp/
*.tmp
```

最需要人工维护的是：

```text
data/source/*.jsonl
data/annotations.sqlite3
evaluation/annotations/*.jsonl
backups/
```
