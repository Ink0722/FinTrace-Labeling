# FinTrace 标注工具

这是一个非并发版网页标注工具。原始 `data/source/questions.jsonl` 保持只读，标注结果实时写入 `data/annotations.sqlite3`，并可导出到 `evaluation/annotations/questions_annotated_v1.jsonl`。

## 启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

首次启动会自动读取 `data/source/questions.jsonl`，按 Session 内顺序生成 `case_id` 和 `turn_id`。

## 数据文件目录

源数据统一放在：

```text
data/source/
```

当前包含：

```text
data/source/questions.jsonl
data/source/documents.jsonl
data/source/chunks.jsonl
data/source/chunks_v2.jsonl
```

这些文件通常体积较大，不建议提交到 GitHub。数据库和导出结果分别保存在：

```text
data/annotations.sqlite3
evaluation/annotations/questions_annotated_v1.jsonl
```

## 导出

网页点击“导出 JSONL”，或访问：

```text
http://127.0.0.1:8000/api/export/jsonl
```

导出文件路径：

```text
evaluation/annotations/questions_annotated_v1.jsonl
```

## 导入 Chunk

Chunk 支持多版本导入。每个版本不会覆盖旧版本。

先导入 Document 元数据，Dashboard 会用它展示公司、标题、发布日期、标签和来源：

```bash
python -m app.import_documents --file data/source/documents.jsonl
```

再导入 Chunk：

```bash
python -m app.import_chunks --file data/source/chunks_v2.jsonl --activate
```

参数说明：

- Chunk 文件每行必须包含一致的 `chunk_version`。
- 重复版本会拒绝导入。
- `--activate` 会把该版本设为 Dashboard 默认搜索版本。

Chunk Dashboard：

```text
http://127.0.0.1:8000/chunks.html
```

从标注页点击“打开 Chunk Dashboard”会自动带上当前 `case_id`，可在 Dashboard 中一键把 chunk 加入当前标注。

## 服务器维护手册

### 推荐目录

服务器上建议把项目放在一个固定目录，例如：

```bash
/opt/fintrace-labeling
```

后续所有维护命令都在项目根目录执行：

```bash
cd /opt/fintrace-labeling
```

需要确保以下目录对运行服务的用户可写：

```text
data/
evaluation/annotations/
```

其中 `data/annotations.sqlite3` 是实时标注数据库，`evaluation/annotations/questions_annotated_v1.jsonl` 是导出的 JSONL 文件。

### 启动或重启服务

开发或临时部署可以直接运行：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

长期放在服务器上建议使用 `systemd` 或类似进程管理工具托管服务。示例服务名为 `fintrace-labeling` 时，常用命令如下：

```bash
sudo systemctl restart fintrace-labeling
sudo systemctl status fintrace-labeling
sudo journalctl -u fintrace-labeling -f
```

对外访问时，浏览器地址通常是：

```text
http://服务器IP:8000
```

如果前面配置了 Nginx 反向代理，也可以使用绑定的域名访问。

### 日常下载标注结果

方式一：浏览器下载。

```text
http://服务器IP:8000/api/export/jsonl
```

方式二：在服务器上生成并查看导出文件。

```bash
python -c "from app.db import export_jsonl; print(export_jsonl())"
```

导出结果会写入：

```text
evaluation/annotations/questions_annotated_v1.jsonl
```

方式三：从本地机器拉取服务器文件。

```bash
scp user@服务器IP:/opt/fintrace-labeling/evaluation/annotations/questions_annotated_v1.jsonl .
```

### 添加新的 Chunk 方案

后续新的 chunk 文件必须包含 `chunk_version` 字段，并且同一个 JSONL 文件内所有行的 `chunk_version` 必须一致。

示例：

```json
{"chunk_id":"...","document_id":"...","chunk_version":"chunks-v3","text":"..."}
```

导入新版本并设为默认搜索版本：

```bash
python -m app.import_chunks --file chunks_v3.jsonl --activate
```

如果需要先更新或补充文档元数据，先执行：

```bash
python -m app.import_documents --file data/source/documents.jsonl
```

注意事项：

- 新版本 chunk 不会覆盖旧版本，数据库会保留历史版本。
- 已存在的 `chunk_version` 会拒绝重复导入，避免误覆盖。
- `--activate` 只影响 Chunk Dashboard 默认展示和搜索的版本，不会删除旧数据。
- 当前标注中已经加入的 chunk 会记录对应 `chunk_version`。

### 切换已有 Chunk 版本

如果某个版本已经导入，只是想切换 Dashboard 默认版本，可以调用接口：

```bash
curl -X POST http://127.0.0.1:8000/api/chunk-versions/chunks-v2/activate
```

把 `chunks-v2` 替换成需要启用的版本号即可。

### 备份数据库

在重置标注、替换服务器、升级代码或批量导入数据之前，建议先备份：

```bash
mkdir -p backups
cp data/annotations.sqlite3 backups/annotations.$(date +%Y%m%d-%H%M%S).sqlite3
```

恢复时停止服务后，把备份文件复制回 `data/annotations.sqlite3`，再重启服务。

### 清空现有编辑记录

如果需要重新开始一轮标注，先备份数据库，然后执行：

```bash
python -m app.reset_annotations
```

这个命令会：

- 清空所有问题的标注字段。
- 清空标注历史记录。
- 将默认 chunk 版本切回 `chunks-v2`。
- 重新导出一份空标注状态的 JSONL。

执行后建议刷新网页确认统计信息已经回到未标注状态。

### 标注员与 Chunk Dashboard

标注员 ID 在主标注页左侧填写。保存标注和从 Chunk Dashboard 加入 chunk 时，都会把该标注员写入当前 case 的更新时间信息中，并在页面上显示为：

```text
更新于 YYYY-MM-DD HH:mm:ss（北京时间） · by 标注员ID
```

建议标注者从主标注页点击“打开 Chunk Dashboard”进入 Dashboard，这样系统会自动把当前 `case_id` 和标注员 ID 带过去。
