# Atlas 场景交换兼容规范（v1）

本文件定义 `atlas-scene` v1 交换包的线上格式、身份语义、规范编码、接受与拒绝边界，
并说明它与当前「导入 → 场景修订 → 冻结 → 渲染 → 验证」链的实际关系。
目标是让第三方在不阅读 Atlas 实现的前提下实现可互操作的读写端，并能通过
[`docs/exchange-vectors/`](exchange-vectors/) 中的向量自查。

本文件的规范强度用以下词表示：**必须**（MUST）、**不得**（MUST NOT）、
**应当**（SHOULD）、**可以**（MAY）。附录中的「参考实现观察」只描述当前 Go 实现
的行为，不构成对他实现的强制要求；但凡是直接影响互操作的，都已提升为正文规则。

---

## 1. 关键选择：交换的是创作材料，不是冻结帧的复现凭据

`atlas-scene` v1 交换的是**可再编辑的创作材料**：一份场景文档，加上它引用的
素材闭包（每个素材的字节被钉在包里）。它**不是**某次被冻结画面的复现凭据。

这个选择不是偏好，而是当前链的直接推论：

1. 导出只序列化快照记录本身，**不包含任何已渲染的 PNG 结果字节**。包里没有 artifact、
   没有 job、没有请求倍数、没有幂等收据。凭据若要复现一次具体输出，至少需要能
   指向那次输出；v1 没有这个能力。
2. 导入**不重建快照记录**。它在目标实例上创建一个**新的** `active` 场景，
   `revision` 从 1 开始，源快照的 `id`、`sceneId`、`revision`、`state`、`createdAt`
   全部被丢弃。实测：把源包导入目标后，按源快照 ID 查询返回 `404`。
3. 导入结果是**立即可编辑**的：对导入场景执行 `PUT /v1/scenes/{id}` 会正常增修订。
   若把它当作「被冻结画面」，导入本身就应该拒绝后续编辑。
4. 撤回（revoke）是源实例上的状态转换。撤回后的快照**仍然可以导出**，其包
   **仍然可以被导入**（实测导入返回 `201`）。如果包是「凭据」，撤回应当阻止它以
   凭据身份流通；v1 没有这个门禁。

因此 v1 能承诺的是**材料确定性**：给定同一个包，绘制语义是确定的，重新冻结并
渲染得到的是由材料决定的画面。参考实现在两个独立进程上得到**逐字节相同**的 PNG
（同一实现内部，见 §7）。跨实现时，v1 保证的是**语义与材料**的确定性，像素级
一致性属于提案 P1，字节级一致性不作承诺。无论哪种情况，v1 都**不**承诺、也
**不得**被解释为「这个包能证明或重放过去某次具体渲染」。

这个选择一旦确定，下面三类问题就必须分开解释，不能混写：

| 问题 | 材料视角（v1 采用） | 凭据视角（v1 不支持） |
|---|---|---|
| 素材退役 | 退役是**目标实例**的策略；包内素材状态不是权威 | 退役应当随凭据传播并阻止导入 |
| 目标同名内容 | 目标是新的创作载体；目标 ID 必须未使用 | 目标应能按凭据定位并核对历史输出 |
| 包摘要 | 内部摘要标识材料；外部摘要标识单条快照记录 | 外部摘要应成为可跨实例比对的输出身份 |

---

## 2. 术语与两层摘要

### 2.1 信封

```
{
  "format":  "atlas-scene",
  "version": 1,
  "snapshot": { ...快照记录... },
  "digest":   "<外部摘要>"
}
```

- `format` **必须**等于 `atlas-scene`。
- `version` **必须**等于 `1`。
- `snapshot` 是 §3 定义的单条快照记录。
- `digest` 是**外部摘要**，定义见 §2.3。

### 2.2 内部摘要（材料身份）

```
inner = SHA-256( UTF8( canonical({ "Document": <文档>, "Assets": <素材映射> }) ) )
```

- 注意成员名是**大写** `Document` / `Assets`，这是 v1 的固定拼写，**不得**改成小写。
- 覆盖：文档的全部语义字段；每个素材的 `id`、`digest`、`state`、`width`、`height`、`png`。
- 不覆盖：快照的 `id`、`sceneId`、`revision`、`state`、`createdAt`。
- `inner` 存放在快照记录的 `snapshot.digest`。

内部摘要是 v1 中唯一可以被当作**材料身份**比对的摘要，但必须连同 §5.3 的归一化
条件一起理解，否则会把「同材料」判错。

### 2.3 外部摘要（记录完整性）

```
outer = SHA-256( UTF8( canonical( <完整快照记录> ) ) )
```

- 覆盖快照记录的全部成员，**包括** `id`、`sceneId`、`revision`、`state`、
  `createdAt`，以及内部摘要与素材记录的 `state`。
- 因为覆盖 `id` / `sceneId` / `revision` / `createdAt`，它在导出 → 导入 → 重新冻结
  之后**不会保持原值**：重新冻结会分配新的快照 ID 与 `createdAt`。实测：同一个源包
  在目标上重新冻结，内部摘要保持一致，外部摘要不一致（见 §7）。读取端**不得**把
  「外部摘要相同」当作「同一条快照记录在另一实例上被重建」的证据。
- 外部摘要是**单条记录的完整性校验**，不是跨实例的材料身份，也**不得**当作
  「同一张画面」的凭据。

### 2.4 两层摘要回答的问题

| 摘要 | 回答 | 可跨实例比对？ | 备注 |
|---|---|---|---|
| 内部摘要 | 「这份文档与这份素材闭包是否逐字段一致？」 | 仅在同一归一化前提下 | 覆盖素材 `state`，见 §5.3 |
| 外部摘要 | 「这条快照记录是否被完整、未被改写地传输？」 | 否 | 覆盖出处字段与 `createdAt` 表示 |

两层摘要都是**不加密钥的 SHA-256**：它们能发现损坏与意外改写，**不能**证明来源或
防伪。任何能构造 JSON 的一方都能铸造一个自洽的包。

---

## 3. 快照记录

### 3.1 成员

| 成员 | 类型 | 说明 |
|---|---|---|
| `id` | string | 源实例的快照 ID；导入后不被沿用 |
| `sceneId` | string | 源实例的场景 ID；导入后不被沿用 |
| `revision` | int | 源场景冻结时的修订号；导入后不沿用 |
| `state` | string | 源快照状态（`active` / `revoked`）；导入时**不校验、不沿用** |
| `document` | object | §3.2 |
| `assets` | object \| null | 素材闭包，键是素材 ID；零素材时为 `null` 或 `{}` |
| `digest` | string | 内部摘要（§2.2），64 位小写十六进制 |
| `createdAt` | string | RFC 3339 时间戳；表示形式参与外部摘要 |

v1 **不校验** `id`、`sceneId`、`revision`、`state` 的取值或它们之间的一致性。
一个包可以携带空 `id`、`revision: 0` 或 `state: "bogus"` 并通过格式校验。
这是有意留给「记录只作装饰」的设计余量，也是 §8 的审查点。

### 3.2 文档

| 成员 | 类型 | 约束 |
|---|---|---|
| `width` | int | 1–1536 |
| `height` | int | 1–1536 |
| `background` | string | `#RRGGBB` |
| `layers` | array \| null | 最多 200 个节点，嵌套最多 8 层 |

图层成员（顺序即规范编码顺序）：

| 成员 | 类型 | 何时出现 |
|---|---|---|
| `id` | string | 始终；`^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`，整棵树内唯一 |
| `kind` | string | 始终；`rectangle` / `ellipse` / `polygon` / `image` / `group` |
| `width` | number | 始终 |
| `height` | number | 始终 |
| `transform` | object | 始终 |
| `opacity` | number | 始终；0–1 |
| `fill` | object | 始终 |
| `points` | array | 仅 `polygon` 且点数非空时 |
| `assetId` | string | 仅 `image` 且非空时 |
| `clip` | object | 仅当存在裁剪 |
| `children` | array | 仅 `group` 且非空时 |

`transform`：`x`、`y`、`scaleX`、`scaleY`、`degrees`，均为 number。
`scaleX` / `scaleY` 的绝对值必须在 0.001–100 之间（0 会被拒绝）。

`clip`：`x`、`y`、`width`、`height`，均为 number，`width` / `height` 必须为正。

`fill`：`kind`（`solid` / `linear`）、`color`、`endColor`（仅 `linear` 且非空时）、
`from`、`to`。颜色必须是 `#RRGGBB`。`linear` 的 `from` 与 `to` 不得重合。

非 `group` 节点必须有正尺寸且不超过 4096；`polygon` 顶点必须在自身局部边界内；
`image` 必须有合法的 `assetId`。

### 3.3 素材记录

| 成员 | 类型 | 约束 |
|---|---|---|
| `id` | string | 必须等于所在映射键 |
| `digest` | string | 必须等于 `SHA-256(png)` |
| `state` | string | 字符串；v1 **不校验**取值，导入时被覆盖为 `available` |
| `width` | int | 1–1024，且必须等于 PNG 实际宽度 |
| `height` | int | 1–1024，且必须等于 PNG 实际高度 |
| `png` | string | 标准 base64（带 `=` 填充，**可以**夹带 CR/LF）；解码后 1 字节–2 MiB，必须是合法 PNG |

关于 `png` 的 base64：v1 只接受**标准字母表**。URL-safe 字母表（`-`、`_`）与去掉
填充的写法都会被拒绝（实测均返回 `400`）；夹带的换行会被解码器忽略（实测接受）。
写入端**应当**输出无换行的标准 base64。

素材闭包规则：`assets` 的键集合**必须恰好等于**文档中 `kind == "image"` 图层
`assetId` 的去重集合。多一个、少一个都拒绝。

注意两个拒绝路径的错误码不同，读取端应当都当作拒绝处理，不要依赖其中一个：

- 闭包**条目数**与引用数不同 → `snapshot asset closure mismatch`；
- 条目数相同但键集合不同（用一个键换掉另一个）→ `snapshot asset missing`。

---

## 4. 规范编码（摘要前像）

摘要不是对收到的字节计算，而是对**解码后的语义值重新做规范编码**再计算。
这解释了两件事：

- 线上字节可以是非规范的（重复成员、`1.0` 写整数、省略零值字段），只要解码出的
  语义值相同，摘要就相同，因此会被接受（见向量 `accept-noncanonical-wire`）。
- 任何改变语义值的字节改动都会改变摘要，从而被发现。

规范编码规则：

1. **结构体成员按声明顺序**输出。§3 的表格顺序就是声明顺序。
   不要把结构体当映射排序。
2. **映射按键的 UTF-8 字节序升序**输出。`assets` 是映射。
3. **省略规则**：带 `omitempty` 的成员在其值为零值时省略。对字符串是 `""`，
   对切片/映射是 `nil` 或长度 0，对指针是 `nil`。没有 `omitempty` 的成员总是输出。
   v1 中带 `omitempty` 的成员只有：图层的 `points`、`assetId`、`clip`、`children`，
   以及 `fill` 的 `endColor`。`fill` 本身、`transform`、`clip` 内部的四个数字都
   **没有** `omitempty`，总是全部输出。
4. **null**：`nil` 映射与 `nil` 切片编码为 `null`；空映射 `{}`、空切片 `[]` 各自编码。
5. **字符串**：JSON 字符串，采用 Go `encoding/json` 的转义：
   - `"` → `\"`，`\` → `\\`；
   - `\b`、`\f`、`\n`、`\r`、`\t` 用短转义；
   - 其余 U+0000–U+001F → `\u00xx`（小写十六进制，4 位）；
   - `<` → `\u003c`，`>` → `\u003e`，`&` → `\u0026`；
   - U+2028 → `\u2028`，U+2029 → `\u2029`；
   - 非法 UTF-8 字节序列解码为 U+FFFD；
   - 其余字符按 UTF-8 原样输出（不做 `\u` 转义）。
6. **整数**：十进制，无前导零，负数带 `-`。
7. **浮点数**：最短可往返表示，并满足：
   - `1e-6 ≤ |x| < 1e21` 用定点（如 `0.000001`、`100000000000000000000`）；
   - 否则用指数形式 `d[.ddd]e±n`，指数不带前导零、必须带符号（如 `1e-7`、`1e+21`）；
   - 负零写作 `-0`，正零写作 `0`；
   - 非有限值（NaN / ±Inf）无法表示，出现即视为非法。
8. **时间戳**：先解析为时刻，再按 Go `time.Time` 的 RFC 3339 形式重新输出：
   - 小数秒保留最短位数（去掉末尾 0）；为零时不写小数点；
   - **保留非零时区偏移**，不归一化为 UTC；
   - **零偏移一律写作 `Z`**：`+00:00`、`-00:00` 与 `Z` 编码结果相同。
   因此同一时刻的 `Z` 与 `+08:00` 会产生**不同**的外部摘要，而 `Z` 与 `+00:00`
   产生**相同**的外部摘要；内部摘要不含 `createdAt`，不受影响。

### 4.1 参考实现的规范编码微向量

第三方实现应当能复算这些值（`→` 右侧是规范编码结果）：

```
map[string]int{"b":1,"A":2,"_":3,"a":4}      → {"A":2,"_":3,"a":4,"b":1}
struct{Z int `json:"z"`; A int `json:"a"`}{1,2} → {"z":1,"a":2}
"a&b<c>d"                                     → "a\u0026b\u003cc\u003ed"
"é" + U+2028 + U+0000                         → "é\u2028\u0000"
0.000001                                      → 0.000001
0.0000001                                     → 1e-7
1e20                                          → 100000000000000000000
1e21                                          → 1e+21
-0.0                                          → -0
1/3                                           → 0.3333333333333333
nil map / empty map                           → null / {}
nil slice / empty slice                       → null / []
内部前像的最小形状：{"Document":{"width":8},"Assets":{}}
```

---

## 5. 身份模型：哪些必须保持，哪些等价不能推出可替换

这一节是本规范的核心。它把「渲染出来一样」与「材料身份相同」严格分开。

### 5.1 必须保持的身份（MUST）

1. **素材字节身份**：`asset.digest` **必须**等于 `SHA-256(asset.png)` 的 64 位小写
   十六进制。任何写入端都**不得**用等价的另一份 PNG 编码替换同一素材 ID 的字节。
2. **素材键身份**：`assets` 的映射键**必须**等于素材记录的 `id`，且**必须**覆盖文档
   引用的全部 `assetId`，不多不少。
3. **像素尺寸身份**：`asset.width` / `asset.height` **必须**等于 PNG 解码后的实际
   尺寸，且在 1–1024 内。
4. **文档语义身份**：文档的每个语义字段都**必须**逐值保持，包括会进入规范前像的
   默认零值。写入端**不得**依赖「渲染器看不出来」来省略或替换语义字段。
5. **内部摘要**：读取端**必须**按 §2.2 与 §4 重新计算并核对。内部摘要是 v1 的材料
   身份。
6. **外部摘要**：读取端**必须**按 §2.3 重新计算并核对，但**不得**把它当作跨实例的
   材料身份或输出凭据。

### 5.2 视觉等价不能推出可替换（MUST NOT）

以下推论都是错的，规范明确禁止：

- 「两份 PNG 解码像素相同，所以同一素材 ID 可以换成其中任一份。」
  **错。** 素材身份是**字节**的 SHA-256，不是像素。同像素不同编码是不同的素材字节。
- 「两份材料渲染出的 PNG 逐字节相同，所以内部摘要应当相同。」
  **错。** 内部摘要覆盖素材字节；渲染相同不改变字节身份。
- 「外部摘要不同，所以材料不同。」
  **错。** 外部摘要覆盖出处字段与 `createdAt` 表示，材料可以完全相同。
- 「渲染结果一致，所以可以跳过摘要核对。」
  **错。** 摘要核对是格式规则，不是可选的优化。

### 5.3 归一化：内部摘要何时能保持

导出 → 导入 → 重新冻结之后，内部摘要**仅在以下条件同时成立时**保持不变：

1. 包里每个素材记录的 `state` 都是 `available`。导入会把素材状态强制写成
   `available`；若包里记录的是 `retired` 或其他值，重新冻结后的内部摘要会不同。
2. `assets` 不是 `null`。导入后重新冻结总是写入 `{}`（而不是 `null`）；因此
   `"assets": null` 的包在目标上会得到不同的内部摘要。
3. 文档在规范化后逐值一致（包含 `layers: null` 这类空值表示）。

`layers: null` 会被原样保留，并在重新冻结时仍是 `null`，因此不影响内部摘要。
`assets: null` 与 `assets: {}` 不能互换，见上。

推论：若需要跨实例的稳定材料身份，**应当**先把闭包归一化（全部素材 `available`、
映射非 `null`）再计算内部摘要，而不是直接比较包里的 `snapshot.digest`。

### 5.4 证据：同像素、不同 PNG 编码

`docs/exchange-vectors/assets/` 提供四份 2×2 素材，解码像素完全相同：

| 素材 | 字节数 | 颜色类型 | 结构差异 | 字节摘要（前 16 位） |
|---|---|---|---|---|
| `plain.png` | 86 | 6 (RGBA) | 单个 IDAT | `aa521fb334b914c7…` |
| `text.png` | 117 | 6 (RGBA) | 额外 `tEXt` 块 | `9d17fb4812a1c22a…` |
| `rgb.png` | 82 | 2 (RGB) | 无 alpha 通道 | `7cb267f8e39f68b8…` |
| `splitidat.png` | 98 | 6 (RGBA) | IDAT 分成两段 | `bc97f93d59171c05…` |

在同一份 `material-rich` 文档下逐一替换素材并渲染，实测：

- 四者渲染出的 PNG 在缩放 1 与缩放 2 下**逐字节相同**
  （缩放 1：`32d7327b…`，缩放 2：`4a5d5972…`）；
- 四者的素材字节摘要**两两不同**；
- 四者的内部摘要**两两不同**；
- 把以 `plain` 构建的包导入一个已经以 `text`（或 `rgb`、`splitidat`）登记了同名素材
  且状态为 `available` 的实例，返回 **`409 destination asset conflicts with package`**，
  且目标场景**不会被创建**；反方向同样冲突。

结论：视觉等价在渲染层是真实成立的，但它**不能**被用来推断交换层的素材可替换性。
交换层的相等判定是**字节摘要相等**。

---

## 6. 接受与拒绝边界（v1 当前实现）

### 6.1 检查顺序

HTTP 导入 `POST /v1/imports/{id}` 的检查是**有序**的，第一个失败的检查决定错误。
顺序如下（已用组合缺陷实测确认优先级）：

| # | 检查 | 失败结果 |
|---|---|---|
| 1 | 请求体 ≤ 12 MiB（`http.MaxBytesReader`） | 400 `invalid JSON: http: request body too large` |
| 2 | JSON 语法；拒绝未知成员；拒绝第二个顶层值 | 400 `invalid JSON: …` |
| 3 | 目标 `id` 合法、`name` 长度 1–160 字节 | 400 `invalid import destination` |
| 4 | `format == "atlas-scene"` 且 `version == 1` | 400 `unsupported scene package` |
| 5 | 外部摘要与快照记录语义值一致 | 400 `package checksum mismatch` |
| 6 | 文档通过场景校验 | 400 `layer …` / `canvas …` 等 |
| 7 | 内部摘要与文档 + 闭包一致 | 400 `snapshot digest mismatch` |
| 8 | 闭包条目数与引用数相同 | 400 `snapshot asset closure mismatch` |
| 9 | 每个引用的键存在且素材 `id` 与键一致 | 400 `snapshot asset missing` |
| 10 | 素材摘要与 `png` 字节一致 | 400 `asset digest mismatch` |
| 11 | `png` 可解码、1 字节–2 MiB、尺寸 1–1024 | 400 `PNG …` |
| 12 | 声明的 `width` / `height` 等于 PNG 实际尺寸 | 400 `asset dimensions mismatch` |
| 13 | 目标场景 ID 未被占用 | 409 `destination scene already exists` |
| 14 | 每个素材：目标不存在则写入；存在则摘要相同且状态 `available` | 409 `destination asset conflicts with package` |
| 15 | 事务内再次执行状态校验，然后原子提交 | 500 或 409 |

第 1–2 步在 HTTP 层；第 3–14 步在服务层；第 15 步是持久化层的事务内校验。
任何一步失败都**不写入任何内容**（场景与全部素材一起回滚）。

该顺序已用「同时违反两条规则」的包逐一实测：目标 ID 与版本同时非法时报目标错误；
版本与外部摘要同时非法时报版本错误；外部摘要与文档同时非法时报摘要错误；内部摘要
与闭包同时非法时报内部摘要错误。读取端可以依赖这个顺序做诊断，但**不得**依赖它做
安全性判断——任何一步的失败都只意味着「拒绝」。

### 6.2 向量边界表

`docs/exchange-vectors/manifest.json` 是权威清单位，共 20 个向量。以下状态均为在
真实进程上观测所得。`错误类` 列是清单里的 `errorClass`，是第三方应当依赖的稳定
类别；「观测文本」列是当前实现的措辞，只作诊断参考，不构成互操作要求。

| 向量 | 期望 | 状态 | 错误类 | 观测文本 / 原因 |
|---|---|---|---|---|
| `material-minimal` | accept | 201 | — | 单素材最小材料 |
| `material-rich` | accept | 201 | — | 组、裁剪、渐变、多边形、位图 |
| `accept-normalized-asset-retired` | accept | 201 | — | 包内素材 `retired` 被归一化为 `available`；内部摘要不保持 |
| `accept-normalized-null-assets` | accept | 201 | — | `assets: null`；重新冻结写 `{}`；内部摘要不保持 |
| `accept-offset-createdat` | accept | 201 | — | 非零时区偏移；内部摘要保持，外部摘要随表示变化 |
| `accept-noncanonical-wire` | accept | 201 | — | 重复成员末值生效、`1.0` 写整数、省略零值、`+00:00` 归零；语义一致 |
| `accept-provenance-unchecked` | accept | 201 | — | `id`/`sceneId` 为空、`revision: 0`、`state: "bogus"`；出处字段不校验 |
| `reject-closure-extra` | reject | 400 | `closure-mismatch` | `snapshot asset closure mismatch` |
| `reject-closure-missing` | reject | 400 | `closure-mismatch` | `snapshot asset closure mismatch` |
| `reject-asset-key-mismatch` | reject | 400 | `asset-key-mismatch` | `snapshot asset missing` |
| `reject-asset-digest` | reject | 400 | `asset-digest-mismatch` | `asset digest mismatch` |
| `reject-asset-dimensions` | reject | 400 | `asset-dimensions-mismatch` | `asset dimensions mismatch` |
| `reject-asset-over-1024` | reject | 400 | `asset-dimensions-limit` | `asset dimensions must be 1..1024` |
| `reject-asset-not-png` | reject | 400 | `asset-not-decodable` | `invalid PNG: …` |
| `reject-outer-checksum` | reject | 400 | `outer-checksum-mismatch` | `package checksum mismatch` |
| `reject-inner-checksum` | reject | 400 | `inner-checksum-mismatch` | `snapshot digest mismatch` |
| `reject-unknown-version` | reject | 400 | `unsupported-envelope` | `unsupported scene package` |
| `reject-unknown-format` | reject | 400 | `unsupported-envelope` | `unsupported scene package` |
| `reject-unknown-member` | reject | 400 | `unknown-member` | `invalid JSON: json: unknown field "note"` |
| `reject-document-invalid` | reject | 400 | `invalid-document` | `layer art: invalid opacity or scale` |

### 6.3 明确「不检查」的区域

以下内容在 v1 中被接受，且**不得**被读取端当作已验证的事实：

- `snapshot.id`、`snapshot.sceneId`、`snapshot.revision`、`snapshot.state` 的取值与
  相互一致性。空字符串、`0`、`bogus` 状态都会通过格式校验。
- 素材记录的 `state` 取值。它参与内部摘要，但不参与合法性判定，导入时被覆盖。
- 外部摘要与内部摘要的**来源真实性**。两者都是无密钥摘要，不防伪。
- 包内时间戳的合理性（未来时间、极早时间均可）。
- 目标实例上与包无关的既有素材是否被其他场景引用。
- 导入目标 `name` 的空白内容。导入只要求长度 1–160 字节，全空白名称会被接受
  （`PUT /v1/scenes/{id}` 则要求去空白后非空）。这是两个入口的已知不对称。

这些区域是格式演进时的审查重点，见 §8、§9。

---

## 7. 导入后的语义与跨实例复现

### 7.1 导入做了什么

在目标实例上，导入是**一次原子提交**：

1. 校验包（§6.1 第 3–12 步）。
2. 对每个素材：目标不存在则写入（状态强制 `available`）；存在则要求摘要相同且
   状态 `available`，然后复用，不覆盖。
3. 创建一个新场景：`id` 取路径参数，`name` 取请求体，`revision = 1`，
   `state = active`，`document` 取包内文档。
4. 发出一个 `scene.imported` 事件。**不**为素材发事件，**不**创建快照记录。

导入**不**保留：源快照 `id`、`sceneId`、`revision`、`state`、`createdAt`、
外部摘要，以及源场景的 `name` 与修订历史。

### 7.2 跨实例复现（同一实现）

对 `material-rich` 包在两个独立实例上分别执行导入 → 冻结 → 渲染，实测：

| 项目 | 实例一 | 实例二 | 相同 |
|---|---|---|---|
| 重新冻结的内部摘要 | `34878037…` | `34878037…` | 是 |
| 重新冻结的外部摘要 | 不同 | 不同 | 否（预期） |
| 缩放 1 的 PNG SHA-256 | `32d7327b…` | `32d7327b…` | 是 |
| 缩放 2 的 PNG SHA-256 | `4a5d5972…` | `4a5d5972…` | 是 |

（两个实例各自重新冻结的 `id` 与 `createdAt` 也不同，这正是外部摘要不一致的原因。）

这证明：**材料足以确定性地再生画面**（在同一实现内，逐字节）。它**不**证明包是
历史输出的凭据：包里没有输出字节，重新冻结也会产生新的快照 ID 与时间。

### 7.3 跨实现边界

v1 固定了几何、裁剪、采样、混合与渐变的语义，但**没有**固定输出 PNG 的编码器。
因此：

- 同一实现、不同实例：**应当**逐字节相同（参考实现已验证）。
- 不同实现之间：**可以**像素相同而字节不同；规范**不**承诺逐字节一致。
  跨实现的像素级一致性属于提案，见 §9 P1。

---

## 8. 为未来扩展留下的接受与拒绝边界

v1 的扩展策略是**保守拒绝**：不认识就整体拒绝，不做部分解析。这是可审查的，
因为它把「未知」统一映射为「拒绝」，不会让不同实现对同一字节流产生不同解释。

### 8.1 版本演进

- v1 读取端**必须**拒绝任何 `format != "atlas-scene"` 或 `version != 1` 的包，
  不得尝试降级解析。
- 未来版本**必须**改变 `version`（或 `format`）。只增加成员而不改版本是**破坏性**
  的，因为 v1 读取端拒绝未知成员。
- 未来读取端**可以**接受 v1。接受时应先按 `version` 分支，再按对应 schema 解析。
- 校验顺序固定为「信封 → 版本 → 外部摘要 → 内容」，这样版本不匹配会在摘要错误
  之前被报告，便于诊断。

### 8.2 未知成员

- **HTTP 入口**在每一层拒绝未知成员（`DisallowUnknownFields`）。这覆盖信封、
  快照、文档、图层、变换、填充、裁剪、素材。
- **库入口**（`bundle.Decode`）不拒绝未知成员，因此同一份字节在库入口与 HTTP
  入口可能得到不同结论。第三方实现若要与 HTTP 行为一致，**必须**拒绝未知成员。
  这是已知的实现不一致点，列为审查项，见 §9 P11。
- 未来的可选扩展**不得**依赖「未知成员被忽略」。若要引入扩展点，应新增一个
  显式的、可选的成员并提升 `version`，或在同一版本内定义「未知成员必须拒绝」
  的稳定基线。

### 8.3 建议的扩展审查清单

任何格式变更在合入前应当逐条回答：

1. 新成员是否改变内部摘要的前像？若是，旧包的内部摘要是否仍可独立校验？
2. 新成员是否改变外部摘要的前像？若是，是否存在需要跨版本比对的场景？
3. 新校验是「拒绝」还是「忽略」？是否有明确的错误码？
4. 新成员是否在 HTTP 与库两个入口得到一致处理？
5. 是否有对应的 accept / reject 向量，且能在独立实现上复算摘要？
6. 是否会把「视觉等价」误当成「身份等价」？

---

## 9. 提案：当前实现**未**支持，不得在 v1 中承诺

以下能力常被误认为已具备。它们在 v1 中**不存在**，只能作为后续提案讨论。
任何文档、客户端或集成方都**不得**把它们当作 v1 的保证。

| 编号 | 提案 | 当前状态 | 影响 |
|---|---|---|---|
| P1 | 跨实现的像素/字节级渲染一致性套件 | 未提供；只固定绘制语义，不固定编码器 | 不同实现的 PNG 字节可能不同 |
| P2 | 导入时保留源快照记录（`id`/`revision`/`createdAt`/`state`） | 不支持；导入创建新场景，不建快照 | 外部摘要不可跨实例继承 |
| P3 | 把撤回状态作为导入门禁 | 不支持；撤回包仍可导入（201） | 撤回不是传播性的 |
| P4 | 包签名 / 来源认证 | 不支持；摘要为无密钥 SHA-256 | 只能发现损坏，不能防伪 |
| P5 | 稳定的、与出处和素材状态无关的「材料 ID」 | 不支持；内部摘要覆盖素材 `state`，且 `null`/`{}` 不等价 | 需要调用方自行归一化 |
| P6 | 导入大于 12 MiB 的合法包 | 不支持；导出无上限，导入受限于请求体 12 MiB | 大包可导出但不可导入 |
| P7 | 覆盖 / 合并导入、修订谱系 | 不支持；目标场景必须不存在 | 无法增量更新 |
| P8 | 素材内容寻址去重、垃圾回收 | 不支持 | 同一字节在不同 ID 下重复存储 |
| P9 | 以 artifact 摘要作为复现凭据 | 不支持；包不含 artifact | 无法证明某次具体输出 |
| P10 | 时间戳规范化为 UTC 后再摘要 | 不支持；保留原偏移 | 同刻不同表示产生不同外部摘要 |
| P11 | 统一 HTTP 与库入口的未知成员策略 | 不一致 | 同一字节两种结论 |
| P12 | 大包流式解析 | 不支持；整体读入内存 | 内存随包大小增长 |

> 注意：P6 的 12 MiB 是**请求体**上限，包含 `{"name":…,"package":…}` 外壳与 JSON
> 开销；因此可导入的包严格小于 12 MiB。参考实现实测：约 12.83 MiB 的合法导出包
> 导入时返回 `400 invalid JSON: http: request body too large`。

---

## 10. 一致性检验

`docs/exchange-vectors/` 提供 20 个向量与 `manifest.json`。清单中每个条目记录：

- `file` 与 `fileSha256`：向量文件及其摘要，便于发现改动；
- `expect`（`accept` / `reject`）与 `httpStatus`；
- `errorClass`（仅 reject）与 `error`（当前实现的观测文本）；
- `innerDigest` / `outerDigest`；
- `innerPreimage` / `outerPreimage`：**规范的摘要前像字节**（UTF-8 字符串）。

第三方实现的最小自查流程：

1. 读 `manifest.json`。
2. 对每个向量，解码 JSON，按 §4 重新构造规范前像。
3. 计算 SHA-256，与 `innerDigest` / `outerDigest` 比对，并逐字节比对前像字符串。
4. 按 §6 判断 accept / reject，与 `httpStatus` 比对；reject 向量的错误类别应与
   `errorClass` 一致。**不要**要求逐字复制 `error` 文本——那是单个实现的措辞。

`scripts/exchange_conformance.py` 是一个独立的、纯 Python 的参考校验器，不依赖
Atlas 服务或 Go 代码。它复算了全部向量的两层摘要与前像，并对格式层接受/拒绝
给出与清单一致的结论：

```sh
python3 scripts/exchange_conformance.py docs/exchange-vectors
```

它只校验格式层；`name` 长度、目标场景占用、素材退役等 HTTP/服务层边界由 §6.2
与真实进程观测覆盖。校验器会对向量文件做 SHA-256 比对，因此修改任何向量而不同步
清单都会被报告。

---

## 附录 A：与现有文档的关系

- `PROJECT_SPEC.md` 的「关键规则」描述了冻结与导入的**行为**；本文件补充其
  **交换身份语义**。冲突时以本文件对格式与身份的定义为准。
- `docs/api.md` 是 HTTP 端点清单；导入的语义细节以本文件 §6–§7 为准。
- `docs/operations.md` 描述持久化与恢复；交换包不参与进程恢复。

## 附录 B：v1 不变的摘要前像形状

内部前像（成员顺序固定，`Document` / `Assets` 大写）：

```
{"Document":<文档>,"Assets":<素材映射或 null>}
```

外部前像（成员顺序固定）：

```
{"id":…,"sceneId":…,"revision":…,"state":…,"document":…,"assets":…,"digest":…,"createdAt":…}
```

两者都按 §4 编码。`assets` 是映射，键按字节序排序；其余是结构体，按声明顺序。
