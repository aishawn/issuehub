# TeamAI + GitLab 内网离线部署交接文档

适用环境：Ubuntu 20.04 LTS，x86_64，服务器不能访问外网。

> 建议：Ubuntu 20.04 已较旧。GitLab 版本必须选择仍支持 Ubuntu 20.04 的固定版本；如果可以新建系统，优先使用 Ubuntu 22.04/24.04。

## 一、联网准备机下载

准备一台与服务器同架构（x86_64）的 Ubuntu 机器，下载并复制以下内容到 U 盘或内网摆渡目录：

```text
offline-install/
├── gitlab-debs/       GitLab CE .deb 及所有依赖
├── teamai-source/     TeamAI 源码
├── teamai-wheels/     Python 离线依赖包
├── config/            .env、systemd 服务文件
└── sha256.txt         文件校验值
```

### 1. GitLab CE 安装包

必须下载一个支持 Ubuntu 20.04 的固定 GitLab CE 版本，不要使用 `latest`。

在联网 Ubuntu 20.04 准备机执行：

```bash
sudo apt update
sudo apt install -y apt-rdepends dpkg-dev
mkdir -p ~/offline-install/gitlab-debs
cd ~/offline-install/gitlab-debs

# 将已确认兼容 Ubuntu 20.04 的 gitlab-ce .deb 下载到当前目录
# 然后下载依赖（具体版本以目标 GitLab 版本为准）
apt download gitlab-ce
```

实际生产准备时，建议使用同版本 Ubuntu 20.04 的干净虚拟机安装测试一遍，确认 `.deb` 依赖完整后再交付。

### 2. TeamAI Python 依赖

在联网准备机执行：

```bash
mkdir -p ~/offline-install/teamai-wheels
cd /path/to/teamai
python3 -m pip download \
  -r requirements.txt \
  --dest ~/offline-install/teamai-wheels
tar czf ~/offline-install/teamai-source.tar.gz .
```

还需准备 Python 3.10 或 3.11。Ubuntu 20.04 默认 Python 3.8，可能无法满足新版本依赖；应提前准备对应的 Python `.deb` 包，或使用公司已有 Python 安装包。

生成校验文件：

```bash
cd ~/offline-install
sha256sum gitlab-debs/* teamai-wheels/* teamai-source.tar.gz > sha256.txt
```

## 二、内网服务器安装

### 1. 校验并安装 GitLab

```bash
cd /opt/offline-install
sha256sum -c sha256.txt
sudo dpkg -i gitlab-debs/*.deb
sudo apt-get -f install --no-download
```

如果提示缺少依赖，回到联网准备机补齐对应 `.deb`，不要临时连接外网安装。

配置 GitLab 地址：

```bash
sudo vi /etc/gitlab/gitlab.rb
```

设置：

```ruby
external_url 'http://gitlab.company.local'
```

应用配置：

```bash
sudo gitlab-ctl reconfigure
sudo gitlab-ctl status
sudo cat /etc/gitlab/initial_root_password
```

### 2. 安装 TeamAI

```bash
sudo mkdir -p /opt/teamai /var/lib/teamai
sudo tar xzf /opt/offline-install/teamai-source.tar.gz -C /opt/teamai
cd /opt/teamai
python3 -m venv .venv
.venv/bin/pip install --no-index \
  --find-links /opt/offline-install/teamai-wheels \
  -r requirements.txt
```

创建 `/opt/teamai/.env`：

```ini
APP_DB=/var/lib/teamai/teamai.db
GITLAB_URL=http://gitlab.company.local
GITLAB_TOKEN=glpat-替换为项目Token
GITLAB_PROJECT_ID=替换为项目ID
```

启动测试：

```bash
/opt/teamai/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8787
curl http://127.0.0.1:8787/health
```

浏览器访问：`http://服务器IP:8787`。

## 三、GitLab 项目和 Token

1. 登录 GitLab。
2. 创建项目，例如 `platform/teamai-issues`。
3. 记录项目 ID。
4. 在项目 `Settings -> Access Tokens` 创建 Token。
5. 权限选择 `api`，将 Token 写入 TeamAI `.env`。

## 四、上线检查

- `/health` 返回 `ok: true`。
- TeamAI 创建问题后，GitLab 项目中出现 Issue。
- 追加解决方案后，GitLab Issue 描述更新。
- 标记解决后，GitLab Issue 关闭。
- `.env` 和 Token 不提交到 Git。
- 配置 SQLite 数据库定期备份。
- 正式开放前通过 Nginx 或公司统一认证保护 TeamAI；当前应用没有内置登录。

## 五、交接信息

```text
GitLab 地址：____________________________
TeamAI 地址：____________________________
GitLab 项目：____________________________
GitLab 项目 ID：__________________________
服务器 IP：______________________________
数据库路径：/var/lib/teamai/teamai.db
管理员/运维联系人：______________________
```
