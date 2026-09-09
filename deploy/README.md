# 腾讯云部署：piusgo.com

目标实例：上海，110.42.252.12，4 核 / 8 GB / 180 GB。容量需要上线后结合实际负载验证。

本配置将 Next.js、FastAPI、PostgreSQL、邮件工作进程和 Caddy 分开运行，数据库和商品图片与客服附件使用独立持久卷。与根目录的本地数据库 Compose 独立，项目名固定为 `piusgo-cloud`。应用使用受限数据库账号，owner 凭据仅提供给数据库与迁移容器。不自动导入本机用户、订单或演示商品。

## 1. 连接与上传

私钥在自己电脑上保管，不能上传服务器或提交到 Git。确认腾讯云实例已绑定对应公钥，用户名以实例实际配置为准（截图默认 ubuntu）。

```sh
ssh -i '/Users/meiqiuu/Desktop/zoyafng (1).pem' ubuntu@110.42.252.12
```

服务器需要 Docker Engine 和 Docker Compose v2，以及到镜像仓库、npm 和 PyPI 的出站访问。先检查已有容器、端口和磁盘，勿覆盖其他业务。将仓库代码上传至独立目录，例如 `/opt/piusgo`；排除 `.env*`、`.git`、虚拟环境、node_modules、.next、backend/data 和 backend/backups。本地数据库如需迁移，另行制作经过确认的备份，勿直接复制旧 SQLite。

## 2. 备案审核期间的私下验收

腾讯云对于首次备案 / 新增网站要求不能通过公网域名或 IP 访问。保持网站 DNS 解析暂停、云防火墙网站端口关闭；SSH 仅放行管理者 IP。不要暴露 3000、8000、5432、8080、8443。

以下命令在服务器仓库根目录执行：

```sh
python3 deploy/init_env.py
# 已有 deploy/.env 时不要重建；生成器会拒绝覆盖。
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml up -d --build
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml ps -a
```

默认监听服务器 `127.0.0.1:8443`，APP_ENV=production、MOCK_MODE=false，保留 Secure Cookie。迁移容器成功退出后才启动后端。首次数据库没有商品，在后台新增正式商品即可；不运行 `backend.seed`。

在自己的电脑建立隧道：

```sh
ssh -N -L 8443:127.0.0.1:8443 -i '/Users/meiqiuu/Desktop/zoyafng (1).pem' ubuntu@110.42.252.12
```

访问 `https://localhost:8443`。私下验收使用 Caddy 内部 CA，浏览器最初会提示证书未信任。可从服务器导出该项目 CA 的**公有根证书**，核实后仅在管理电脑信任它；不导出 CA 私钥。不要把临时证书用于公网，也不要全局关闭浏览器证书校验。

```sh
# 在服务器导出公有证书，再复制到管理电脑。
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml cp proxy:/data/caddy/pki/authorities/local/root.crt /tmp/piusgo-root.crt
# 在服务器用证书检查私下入口与前后端链路。
curl --cacert /tmp/piusgo-root.crt --resolve localhost:8443:127.0.0.1 https://localhost:8443/api/products
```

注册自己的账号，再在服务器授予后台权限（替换邮箱），重新登录后访问 `/admin`：

```sh
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml exec backend python -m backend.admin_access YOUR_EMAIL
```

检查注册、验证码登录、后台新增和上传图片、首页展示、容器重启后数据仍存在。API 和反向代理不启用访问日志，避免游客订单凭据写入 URL 日志。当前后端不信任转发 IP，登录记录可能显示内部代理 IP；上线前应明确代理信任链后再调整。

## 3. 邮件与真实交易

在服务器 `deploy/.env` 填写 SMTP_HOST、SMTP_PORT、SMTP_USER、SMTP_PASSWORD、SMTP_FROM，权限保持 600。不要在聊天或日志中输出凭据。

```sh
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml --profile mail up -d
```

工作进程默认不启动，避免无 SMTP 时消耗重试次数。配置后应使用运营者自己的测试邮箱验收。当前验证码邮件接口与订单交付邮件的实现不同，应逐项验证，不能仅凭 worker 运行就视为邮件功能完成。

**当前系统还没有真实支付网关与真实卡密供货，生产模式禁用模拟支付、充值、抽奖等能力。容器部署成功不代表商城可以真实收款。** 应完成支付签名回调、交付、商品与内容审核、邮件验收后再开展交易；不通过启用 mock 绕过限制。

## 4. 备案通过后启用公网 HTTPS

确认腾讯云已同步备案成功、业务准备完成，并在网站页脚加入真实备案号与官方查询链接。仅在此时：

1. 添加 A 记录：`@` → `110.42.252.12`；若使用 www，同样添加 `www` → 该 IP。没有配置 IPv6 就不添加 AAAA。
2. 修改服务器 deploy/.env：

```dotenv
SITE_URL=https://piusgo.com
BIND_ADDRESS=0.0.0.0
HTTP_PORT=80
HTTPS_PORT=443
CADDY_CONFIG=./Caddyfile.public
```

3. 腾讯云防火墙和系统防火墙放行 TCP 80、443；保留受限 SSH，不开放数据库和应用端口。
4. 重新构建启动（SITE_URL 也参与前端构建）：

```sh
docker compose -p piusgo-cloud --env-file deploy/.env -f deploy/compose.yaml --profile mail up -d --build
curl -I https://piusgo.com
curl --fail https://piusgo.com/api/products
```

仅在 SMTP 已配置时使用 `--profile mail`。Caddy 自动申请并续期公网证书，证书状态保存在独立卷。www 自动跳转到主域名；检查 DNS、证书、HTTP 跳转、登录 Cookie、写请求 Origin、商品图片和重启恢复。INDEX_SITE 仍为 false，正式内容审核完成后再单独开启抓取。

## 5. 备份、更新与恢复

```sh
bash deploy/backup.sh
```

该脚本短暂停止当前运行的应用与邮件进程，备份数据库、商品图片及客服附件，结束后恢复原先运行的服务；数据库不停止。备份保存在 deploy/backups，目录不入 Git。保留失败时的 .tmp 用于排查；只把成对的 `.dump` 和 `-files.tar.gz` 完成文件视为成功备份。另行加密保存服务器 `.env`，将备份复制到异机存储，设置留存周期和定时任务；脚本本身不会自动定时或执行异机复制。

每次更新前先备份，保存旧代码版本，再执行上述 up --build。迁移失败时先检查 migrate 日志；不要删除数据卷，不使用 `down -v`。迁移成功后的代码回滚必须核对数据库兼容性，不自动执行数据库降级。

恢复应先在隔离的新 Compose 项目、新端口和空卷中演练：初始化角色和表结构后，停止应用写入，用同版本 PostgreSQL 的 pg_restore 将备份恢复至 piusgo 数据库（覆盖操作只对明确确认的恢复目标执行），恢复对应图片目录，再运行 backend.grants。核对账号、订单、商品数和图片后才能切换生产。未完成恢复演练前不能宣称备份可用。

备案规则来源：[腾讯云备案审核](https://cloud.tencent.com/document/product/243/19650)。

## 本次验证记录（2026-09-07）

- 前端本地生产构建、Compose 配置校验、脚本语法检查通过；凭据生成器的 0600 权限与拒绝覆盖行为已验证。
- Linux ARM64 前后端 Docker 镜像构建通过。原锁文件遗漏 Linux 所需 greenlet，已补为跨平台锁文件，保留已有依赖版本。
- 独立 `piusgo-cloud-validation` 项目中，从空 PostgreSQL 卷执行迁移（镜像构建时至 0005），应用账号授权成功，前后端健康检查通过。
- 使用测试 Caddy CA 严格校验 HTTPS 首页与 `/api/products`，返回 mock=false、空商品列表；公网 Caddy 配置校验通过。测试容器与测试卷已清理。
- 未完成：目标云服务器部署、Linux AMD64 目标机验收、真实 SMTP、备份恢复演练和公网 DNS/证书验收。SSH 对 ubuntu/root 均返回密钥认证失败，需确认实例密钥绑定与实际用户名。
- 验证期间工作区存在其他并行业务修改；以上结果针对构建时的镜像，不代表后续业务修改和迁移已一并验收。

在线客服工作台为 `/support`，详见 `docs/SUPPORT.md`。新增 `support_attachments` 私有卷与 SSE 代理配置；本轮未执行云端部署。旧 images 备份不包含客服附件，新 files 备份包含两个目录，恢复时须核对对应版本。
