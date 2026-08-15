# FinTrace 标注系统部署与维护文档

本文档用于维护部署在阿里云服务器上的 FinTrace 标注系统。推荐结构：

```text
公网用户 -> Nginx 80/443 -> 127.0.0.1:8000 -> FastAPI/Uvicorn
```

当前服务器信息：

```text
公网 IP: 121.43.58.18
项目目录: /workspace/fintrace-labeling
Python: /root/miniconda3/envs/FinTrace-Labeling/bin/python
systemd 服务: fintrace-labeling
```

## 首次部署

第一次可以整包上传项目。正式标注开始后，不要再整包覆盖服务器目录，避免覆盖 `data/annotations.sqlite3` 和导出结果。

```bash
cd /workspace/fintrace-labeling
conda activate FinTrace-Labeling
pip install -r requirements.txt
python -m app.import_documents --file data/source/documents.jsonl
python -m app.import_chunks --file data/source/chunks_v2.jsonl --activate
```

问题集由系统首次启动时从 `data/source/questions.jsonl` 自动读取。

## systemd 服务

```bash
cat > /etc/systemd/system/fintrace-labeling.service <<'EOF'
[Unit]
Description=FinTrace Labeling Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/workspace/fintrace-labeling
ExecStart=/root/miniconda3/envs/FinTrace-Labeling/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

```bash
systemctl daemon-reload
systemctl enable fintrace-labeling
systemctl restart fintrace-labeling
systemctl status fintrace-labeling
```

检查后端：

```bash
curl http://127.0.0.1:8000/api/stats
journalctl -u fintrace-labeling -f
```

## Nginx 反向代理

```bash
cat > /etc/nginx/conf.d/fintrace-labeling.conf <<'EOF'
server {
    listen 80;
    server_name 121.43.58.18;

    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
    }
}
EOF
```

```bash
nginx -t
systemctl enable nginx
systemctl restart nginx
systemctl status nginx
```

阿里云安全组开放 `TCP 80` 和 `TCP 22`。如果启用了 firewalld：

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --reload
```

访问：

```text
http://121.43.58.18
```

## 绑定域名

DNS 只能把域名解析到 IP，不能解析到 `:8000`。绑定域名时添加 A 记录：

```text
label.example.com -> 121.43.58.18
```

然后把 Nginx 中的 `server_name` 改成：

```nginx
server_name label.example.com;
```

或：

```nginx
server_name 121.43.58.18 label.example.com;
```

改完执行：

```bash
nginx -t
systemctl reload nginx
```

## HTTPS 是否需要

短期内部测试可以先不用 HTTPS。正式多人标注、绑定域名、长期公网开放时，建议启用 HTTPS。

HTTPS 的好处：

- 标注员 ID、标注内容和导出文件传输加密。
- 浏览器不会提示“不安全”。
- 后续加登录、访问控制、域名访问更规范。
- 证书可以自动续期，维护成本低。

推荐方案：

```text
域名 -> Nginx 443 HTTPS -> 127.0.0.1:8000
```

有域名后可用 Certbot：

```bash
yum install -y certbot python3-certbot-nginx
certbot --nginx -d label.example.com
certbot renew --dry-run
```

同时在阿里云安全组开放 `TCP 443`。

## 更新代码

后续推荐用 Git 更新代码：

```bash
cd /workspace/fintrace-labeling
git pull
systemctl restart fintrace-labeling
```

通常不需要重启 Nginx。只有修改域名、HTTPS、Nginx 配置、上传限制或访问控制时才需要：

```bash
nginx -t
systemctl reload nginx
```

## 添加新的 Chunk 版本

新的 chunk JSONL 必须包含 `chunk_version` 字段，且同一个文件内所有行版本一致。

```bash
cd /workspace/fintrace-labeling
conda activate FinTrace-Labeling
python -m app.import_chunks --file data/source/chunks_v3.jsonl --activate
```

如果 Document 元数据也更新：

```bash
python -m app.import_documents --file data/source/documents.jsonl
```

导入新版本不会覆盖旧版本。已存在的 `chunk_version` 会拒绝重复导入。

## 下载标注结果

浏览器下载：

```text
http://121.43.58.18/api/export/jsonl
```

服务器上手动生成：

```bash
cd /workspace/fintrace-labeling
conda activate FinTrace-Labeling
python -c "from app.db import export_jsonl; print(export_jsonl())"
```

导出文件：

```text
evaluation/annotations/questions_annotated_v1.jsonl
```

本地下载：

```bash
scp root@121.43.58.18:/workspace/fintrace-labeling/evaluation/annotations/questions_annotated_v1.jsonl .
```

## 备份和恢复

实时标注数据库：

```text
data/annotations.sqlite3
```

备份：

```bash
cd /workspace/fintrace-labeling
mkdir -p backups
cp data/annotations.sqlite3 backups/annotations.$(date +%Y%m%d-%H%M%S).sqlite3
```

恢复：

```bash
systemctl stop fintrace-labeling
cp backups/需要恢复的备份.sqlite3 data/annotations.sqlite3
systemctl start fintrace-labeling
```

## 清空编辑记录

清空前务必先备份，然后执行：

```bash
python -m app.reset_annotations
```

该命令会清空标注字段和标注历史，并将默认 chunk 版本切回 `chunks-v2`。

## 常用排查

```bash
ss -lntp | grep 8000
ss -lntp | grep ':80'
systemctl status fintrace-labeling
systemctl status nginx
journalctl -u fintrace-labeling -f
tail -f /var/log/nginx/error.log
curl http://127.0.0.1:8000/api/stats
curl http://121.43.58.18/api/stats
```
