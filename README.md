# 清穹 AirScope

面向 OHCP 一小时智能编程大赛的空气质量时序分析 Agent。用户用中文提问，DeepSeek Flash 通过原生 function tool_calls 选择并调用本地数据工具；CSV 清洗、SQLite 查询、统计、国标研判和结果校验均在本机完成。

## 启动

需要 **Python 3.10 或更高版本**和可访问 DeepSeek API 的网络。后端 HTTP 调用使用 Python 标准库，无需新增 pip 依赖。默认模式需要 `DEEPSEEK_API_KEY`；网页加载或刷新不会自动发起模型请求，只有提交分析问题时才会调用 API。

发行包分两种：组内交付压缩包通过内部渠道包含已配置的 `.env.local`，组员解压后可直接运行；公开源码见 [GitHub 仓库](https://github.com/liujiting123/AirScope)，不包含 `.env.local` 或 API 密钥。公开源码的使用者需将 `.env.example` 复制为 `.env.local`，再填写自己的 `DEEPSEEK_API_KEY`。不要把组内压缩包或密钥提交到 GitHub。

- macOS：双击 `启动.command`。
- Windows：双击 `启动.bat`。
- 终端：在项目目录执行 `python3 app.py --open`；Windows 可用 `py -3 app.py --open`。
- 无需模型服务时，可显式执行 `python3 app.py --offline --open`，使用本地规则备用流程。
- 浏览器地址：`http://127.0.0.1:8765`。端口占用时执行 `python3 app.py --port 8766 --open`。

数据已准备在 `data/raw/`，比赛现场无需下载。首次启动自动分片导入 SQLite；后续通过 SHA256 核对数据并复用索引。

随项目提供2024年342个活跃地区、31个省级行政区的125,172条城市日记录。原始数据来自中国环境监测总站城市监测值的 Quotsoft 公开归档；详见数据说明。

交互工作台由本地 Python 服务提供。在线默认模式把用户问题发给 DeepSeek，由模型理解意图并选择工具；原始 CSV 和 SQLite 查询仍留在本机，模型接收工具返回的统计观察后可继续调用工具，最后整理回答。每次分析导出的 `outputs/<任务ID>/report.html` 是自包含文件，可直接双击、离线展示或打印为 PDF。

## 三分钟现场演示

1. 输入：**找出2024年PM2.5年均浓度最高的5个城市，画出月度变化曲线**。展示排名、月度曲线、有效日数与标准研判。
2. 追问：**再看这几个城市的PM10超标情况**。观察结果只涉及上一轮的5个城市。
3. 输入：**筛选PM2.5超标记录，统计各省份超标天数占比**。展示省份城市日超标率与构成图。
4. 输入：**统计北京市夏季臭氧O₃超标总天数**。展示6–8月、日最大8小时平均浓度口径。
5. 打开工具执行记录，再导出 CSV 和 HTML。展示每项统计来自本地工具、每张图都能保存。

还支持“北京市PM2.5月度变化”“北京市PM2.5四季对比”“北京市PM2.5异常跳变”。自然语言接口围绕这五类分析任务设计；年份、月份、城市、省份、污染物、Top N 都可变化。

## Agent 与工具设计

默认在线模式由 DeepSeek Flash 负责理解自然语言问题、选择工具、读取 observation 并决定是否继续调用。`airq/llm_agent.py` 按 DeepSeek 原生 `tool_calls` 协议执行多轮循环，`airq/deepseek.py` 用标准库 HTTP 客户端连接 API；每次调用和返回结果通过实时 NDJSON 事件流显示在工作台。当前会话的上下文支持连续追问，工具记录保留每一步真实执行过程。模型离线或调用失败时会明确返回错误，不会把失败伪装成成功结果。

统计数值仍由本地 Python 工具和 SQLite 产生，模型负责安排调用并解释工具观察。刷新页面不会自动重跑分析；每个结果页签对应用户提交的问题和其工具执行结果。`--offline` 显式启用本地规则备用流程，不连接模型服务。

| 工具 | 职责 | 实现 |
|---|---|---|
| `get_dataset_info` | 返回本地数据覆盖范围、可用城市和年份 | `airq/llm_agent.py` |
| `clean_data` | 清洗并复用本地 CSV 索引 | `airq/llm_agent.py`、`airq/data.py` |
| `get_standard` | 查询赛事指定的 GB 3095—2012 历史口径 | `airq/llm_agent.py`、`airq/standards.py` |
| `analyze_air_quality` | 按结构化计划在本地计算排名、超标、趋势、季节对比或跳变 | `airq/llm_agent.py`、`airq/analysis.py` |
| `validate_result` | 复核统计值、分母、比例和图表长度 | `airq/llm_agent.py`、`airq/agent.py` |
| `render_visualization` | 生成 SVG、CSV、JSON 和独立 HTML | `airq/llm_agent.py`、`airq/charts.py` |

在线循环由模型根据 tool result 选择下一步，原始记录由本地工具按需读取，不交给模型计算统计值。`--offline` 沿用 `airq/agent.py` 与 `airq/planner.py` 的确定性规则流程。CSV 按默认 5,000 行分片处理，原始数据不一次全部放入内存；SQLite 按需筛选和聚合。数据文件或来源配置变化会触发重新导入。异常负值、非数值和缺测记为空；真实污染高峰保留。城市与日期构成唯一键，重复项优先保留第一条有效值，并允许后续记录补齐缺项。

## 依赖

- **Python 3.10+**：只用 Python 标准库，不需要额外 `pip install`。依赖说明见 [`requirements.txt`](requirements.txt)。
- **DeepSeek API**：默认在线模式需要本机网络可访问配置的 HTTPS 接口，默认模型为 `deepseek-flash`。
- **浏览器**：打开本地工作台即可，无需安装前端包或 CDN 资源。

## 统计口径

比赛明确指定 GB 3095—2012；项目采用该版及2018修改单的二级限值分析2024年历史数据。

| 污染物 | 年均限值 | 日统计限值 | 单位 |
|---|---:|---:|---|
| PM2.5 | 35 | 75（24小时平均） | μg/m³ |
| PM10 | 70 | 150（24小时平均） | μg/m³ |
| SO₂ | 60 | 150（24小时平均） | μg/m³ |
| NO₂ | 40 | 80（24小时平均） | μg/m³ |
| CO | — | 4（24小时平均） | mg/m³ |
| O₃ | — | 160（日最大8小时平均） | μg/m³ |

有效观测严格大于对应限值才记为超标，等于限值记为未超标。缺测不进入比例分母。正式年均判断同时要求至少324个有效日、各月至少27日（2月至少25日）；不足时展示样本期均值。月曲线用于描述变化，不套用日均限值作月达标判断。

省份超标率 = 该省超标城市日数 ÷ 该省有效城市日数。表中另列“至少1城超标的不同日期数”，让两种业务问题各有对应指标。冬季指所选自然年内1、2、12月。

GB 3095—2026 已于2026年3月1日实施；此项目的比赛口径保持为题目指定的2012版。来源见 `docs/数据说明.md`。

## 数据与替换

真实公开数据来源、下载清单、原始文件哈希、小时到日的处理过程保存在 `data/source.json`、`data/source-cache/` 和 `docs/数据说明.md`。`scripts/prepare_data.py` 用于赛前复现取数，启动服务不会联网下载。

使用教师数据时，将原演示 CSV 移出 `data/raw/`，放入老师提供的日数据 CSV，再更新 `data/source.json` 的来源信息并重启。避免混放不同数据源的同城同日记录。

规范列：

```text
date,city,province,PM2.5,PM10,SO2,NO2,CO,O3_8h,AQI,quality
```

支持 UTF-8 和 GB18030，日期如 `2024-01-01`，并识别“日期、城市、省份、二氧化硫”等常见别名。`O3_8h` 必须明确表示日最大8小时平均；普通 `O3` 列保留为未明确口径，导入器不将其自动用于8小时超标判断。CO 单位为 mg/m³，其余浓度为 μg/m³。

## 输出与复现

每次查询写入 `outputs/<任务ID>/`：`result.json`、`table.csv`、`report.html`、`chart-*.svg`。结果 JSON 包含查询计划、数据哈希、统计表、图表数据、校验结果和真实调用耗时。查询历史保存在 `outputs/history.jsonl`。

```bash
python3 app.py --query '统计北京市夏季臭氧O₃超标总天数'
python3 -m unittest discover -s tests -v
```

测试覆盖、真实 DeepSeek API 调用、前端交互和数据结果核验的实际命令及通过状态见 [`docs/测试证据.md`](docs/测试证据.md)。测试夹具与展示数据相互隔离。

## 提交清单

- `app.py` 与 `airq/`：完整服务、Agent、计算和绘图源码。
- `web/`：HTML、CSS、JavaScript 本地工作台。
- `data/raw/`：真实数据；`data/source.json`：来源与加工记录。
- `scripts/prepare_data.py`：取数及日值生成脚本。
- `README.md`、`docs/数据说明.md`：启动与数据说明。
- `requirements.txt`、`docs/测试证据.md`：运行依赖与测试验收证据。
- `docs/参赛答辩.pptx`、`docs/答辩讲稿.md`：路演材料。
- `tests/`：关键统计逻辑与规划测试。

OHCP 答辩档按规则文档的六项参考维度综合评审：需求思考与完成度20%、Harness过程与系统架构合理性20%、功能测试覆盖率15%、演讲材料质量15%、人员分工合理性10%、系统亮点20%。题目另列的业务侧重为 Agent 规划与工具调用40%、数据预处理/聚合/统计30%、可视化与生态治理解读20%、代码规范10%；该业务侧重用于解释题目要求，不替代 OHCP 答辩评分维度。作品亮点包括原生 function tool_calls、多轮上下文、本地统计校验和可追溯的实时工具过程。
