# immich-omni

给 Immich 增加一层**中文图片描述 + 1024 维向量检索**能力，数据全部写进 Immich 自带 Postgres 的 **独立 `omni` schema**，不碰 Immich 自己的表，也不直接读挂载的照片目录。

## 它做什么

1. 通过 Immich REST API 拉取图片列表和缩略图（**不访问 NFS**）；
2. 用 [immich-analyze](https://github.com/timasoft/immich-analyze) 调用远端视觉模型 `Qwen3-VL-32B-Instruct-4bit`，生成**中文搜索导向**的描述；
3. 把描述用本地 llama.cpp 的 `Qwen3-Embedding-8B`（Q8_0）编码成 4096 维向量，按 Matryoshka 截断到 **1024 维**并做 L2 归一化；
4. 描述和向量一起写入 `omni.assets`（pgvector + HNSW 索引）；
5. 提供中文查询命令，按余弦相似度返回图片。

```
Immich API ──thumbnail──▶ immich-analyze (patched, batch)
     │                          │ JSONL: {asset_id, status, description}
     │                          ▼
     └──search/metadata──▶  pipeline (Python) ──▶ omni.assets (Postgres)
                                │
                                ▼
                    llama.cpp + Qwen3-Embedding-8B
                         4096-d → 1024-d → L2
                                │
                                ▼
                    omni.assets.embedding (pgvector HNSW)
```

## 为什么 patch immich-analyze

上游 v0.6.1 的 batch 模式只在**全部跑完后**一次性打印结果，中途 Ctrl-C 会丢掉所有已生成的描述，无法满足"随时打断、原地继续"。`docker/immich-analyze/patches/` 里的补丁只加了三件事：

| 参数 | 作用 |
|---|---|
| `--jsonl` | 每处理完一张图，立刻输出一行 JSON（成功/失败/跳过） |
| `--assets-file` | 只处理文件里列出的 asset id，配合数据库里的 pending 队列做断点续跑 |
| `--no-writeback` | 不把描述写回 Immich（避免触发 XMP sidecar，也避免只读挂载下的静默回滚） |

补丁固定打在 `v0.6.1` tag 上，镜像构建时从 GitHub 拉源码并 `patch -p1`，不改上游仓库。

## 前置条件

- Immich v3.x，Postgres 镜像已带 `pgvector`（本项目使用 `vector 0.8.1`）；
- Immich API key：至少 `asset.read`、`asset.view`、`asset.update`；
- 可访问远端视觉模型（默认 `https://mbp-dpsk.tailc4cf9.ts.net`，Tailscale）。Docker bridge 容器**不能**直连 Tailscale，所以 compose 里有一个 `model-gateway` 用 socat 把 `:18081` 转发到 Tailscale IP:443，pipeline 通过 `host-gateway` 访问它，TLS/SNI 仍然是原域名；
- AMD GPU（ROCm，`/dev/kfd` + `/dev/dri`）用于 llama.cpp；没有 GPU 可把 `compose.yaml` 里的镜像换成 CPU 版并去掉 `devices`；
- 构建镜像时需要能访问 GitHub 和 crates.io（用 `BUILD_HTTP_PROXY`）；
- 磁盘：模型约 8.1 GB。

## 配置

```bash
cp .env.example .env
$EDITOR .env
```

必须改的项：

| 变量 | 说明 |
|---|---|
| `IMMICH_API_KEY` | Immich API key |
| `IMMICH_DB_PASSWORD` | Immich 的 Postgres 密码（`self-hosted/immich/.env` 里的 `DB_PASSWORD`） |
| `OMNI_DB_PASSWORD` | 给 `omni` 角色新设的密码 |
| `DESCRIBE_HOST_NAME` | 远端模型域名，用于 `extra_hosts` 指到 `host-gateway` |
| `DESCRIBE_HOST_IP` | 远端模型的 Tailscale IP（`tailscale status` 里的 `mbp-dpsk`），model-gateway 转发目标 |
| `BUILD_HTTP_PROXY` | 构建镜像时的代理，如 `http://127.0.0.1:7890`；不需要就留空 |

`DESCRIBE_HOST` 用域名、`DESCRIBE_HOST_IP` 用 IP，是因为 Docker bridge 容器用不了 Tailscale MagicDNS，compose 会通过 `extra_hosts` 把它钉到 IP 上。

## 快速开始

```bash
# 1. 构建 pipeline 镜像（内置打过补丁的 immich-analyze）
#    需要 GitHub / crates.io 可达；.env 里的 BUILD_HTTP_PROXY 会在构建时生效
make build

# 2. 在宿主编译 llama-server（HIP/gfx1201），并打包成本地镜像
#    需要 ROCm（hipcc）和 cmake；首次会 clone llama.cpp
make llama

# 3. 下载模型（可反复执行，断点续传；默认走 ModelScope，约 6-7 MB/s）
make download

# 4. 启动本地 embedding 服务和 Tailscale 网关
make up
docker compose logs -f llama-embed   # 等到 "server is listening"

# 5. 初始化 omni schema
make init

# 6. 小批量验证
make sync LIMIT=200
make describe LIMIT=5
make embed LIMIT=5
make search Q='陶笛'

# 7. 全量（可随时 Ctrl-C，再执行同一条命令继续）
make run CHUNK=64
```

> `make llama` 是唯一在宿主机上执行的编译步骤：这台机器的 ghcr.io 拉取不稳定，而宿主机已经有匹配的 ROCm 7.2 工具链。运行阶段仍然全部在 Docker 里。

也可以用 compose 直接跑单步：

```bash
docker compose run --rm pipeline describe --limit 32
docker compose run --rm pipeline embed --limit 256
docker compose run --rm pipeline status
```

## 已验证的结果

在 43,842 张图片的库上：

- `sync` 全量镜像 43,842 条元数据；
- 抽样 14 张图片完成 `describe`（中文描述，含 OCR 文字）；
- 全部 14 条完成 1024 维 embedding；
- 中文查询 `陶笛` 命中陶笛图（0.72），第二位是吉他音箱（0.43）；`深蓝色的陶笛` 命中同一张图（0.81）。

全量 describe 的吞吐取决于远端 32B 模型的排队情况：单张大约 10–20 秒，`ANALYZE_CONCURRENCY=2` 时 43k 张约需 60–120 小时。`make run` 可以一直跑，随时 Ctrl-C，再执行同一条命令会从 pending 队列继续。

## 断点续跑

所有状态都在 `omni.assets.status` 里：

```
pending ──describe──▶ described ──embed──▶ embedded
   ▲                        │
   └──── 失败 attempts<3 ───┘
```

- `describe` 每次只捞 `status='pending' AND attempts<3` 的资产，把 id 写到 `/work/assets-*.txt`，交给 immich-analyze；每收到一行 JSON 就单独 commit。中途 kill 最多丢掉正在处理的那几条，重跑会重新排队。
- `embed` 只处理 `status='described' AND embedding IS NULL`，批量提交。
- 连续失败 3 次的资产会变成 `failed`，不再阻塞队列；排查后可以手工重置：
  ```sql
  UPDATE omni.assets SET status='pending', attempts=0 WHERE status='failed';
  ```
- `sync` 是幂等的 upsert，重复执行只更新文件名/时间等元数据，不会覆盖已有描述和向量。

## 中文搜索

```bash
make search Q='深蓝色陶瓷乐器'
make search Q='雨天的城市街道' K=20
```

`QUERY_INSTRUCTION` 会以 Qwen3-Embedding 的 `Instruct: ...\nQuery: ...` 形式加在查询前面，文档侧不加 instruction。描述本身由中文 prompt 生成，所以中文查询和中文描述在同一语义空间里。

当前检索走 `omni.assets` 的 `<=>` 余弦距离 + HNSW，返回 `(score, asset_id, 文件名, 描述)`；要跳回 Immich 可以拼 `http://<immich>/photos/<asset_id>`。

## 数据模型

```sql
omni.assets (
    asset_id uuid primary key,
    original_file_name text,
    file_created_at timestamptz,
    status text,              -- pending/described/embedded/failed
    description text,
    description_model text,
    embedding public.vector(1024),
    embedding_model text,
    attempts int,
    last_error text,
    ...
)
```

`omni` 是独立 schema，`omni` 角色由 `init-db` 创建并持有它；Immich 的 `public` schema 和它自己的迁移完全不受影响。

## 目录结构

```
compose.yaml                     # llama-embed / pipeline / model-download
docker/pipeline/Dockerfile       # Rust 构建打好补丁的 analyzer + Python 运行时
docker/immich-analyze/patches/   # 针对 v0.6.1 的 JSONL/资产过滤补丁
sql/schema.sql                   # omni schema DDL
src/immich_omni/                 # sync / describe / embed / search CLI
scripts/download_model.py        # 无依赖、可续传的并行下载器
```

## 排障

- **`llama-embed` 起不来 / 报 gfx 不支持**：确认 ROCm 版本支持 `gfx1201`；也可以临时把镜像换成 CPU 版，去掉 `devices` 和 `ngl`。
- **describe 全部报错**：看 `work/analyzer.log`；确认 `DESCRIBE_HOST_IP` 正确、远端 `/v1/models` 能列出模型。
- **embed 报 404/400**：确认 llama.cpp 启动参数带 `--embeddings --pooling last`，`/v1/embeddings` 可用。
- **HNSW 索引没生效**：数据量小时 Postgres 可能选择顺序扫描，`EXPLAIN` 确认即可；向量多到几万条后会走索引。
