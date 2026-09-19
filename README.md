# GS1 条码元素串解析 API

基于 Python + FastAPI 的 GS1 元素串（Element String）解析服务。接收扫码枪原始输出
（含 ASCII 29 / FNC1 分隔符）或 GS1 Digital Link URI，返回保持原顺序的结构化元素，
并完成字符集、长度、GTIN Mod-10 校验位与真实日期校验。

## 一键启动

```bash
docker compose up --build
```

启动后访问：

- API 地址：http://localhost:8080
- 交互式文档（Swagger UI）：http://localhost:8080/docs
- 健康检查：http://localhost:8080/health

## 支持的 AI

| AI   | 含义        | 长度            | 字符集                          |
| ---- | ----------- | --------------- | ------------------------------- |
| 01   | GTIN        | 固定 14 位数字  | 0-9，含 Mod-10 校验位验证       |
| 17   | 效期 YYMMDD | 固定 6 位数字   | 0-9，校验真实日期（含闰年）     |
| 10   | 批号        | 可变 1-20 字符  | GS1 AI 字符集 82                |
| 21   | 序列号      | 可变 1-20 字符  | GS1 AI 字符集 82                |

解析规则：

- 固定长度字段（01、17）按长度自动定界，无需分隔符。
- 可变长度字段（10、21）除位于串末尾外，必须以分隔符 ASCII 29（GS，扫码枪 FNC1）
  结束；JSON 中以 `\u001d` 表示。
- AI 17 的日字段为 `00` 时按 GS1 规范表示当月最后一天；年份按 20YY 解释。

## 使用示例（元素串）

请求（注意批号与序列号之间的 `\u001d` 分隔符）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "01095011015300031725013110ABC123\u001d21SN0001"}'
```

响应 `200 OK`，元素按原始顺序返回，`offset` 为 AI 在输入中的字节偏移：

```json
{
  "elements": [
    { "ai": "01", "name": "GTIN",     "value": "09501101530003", "offset": 0  },
    { "ai": "17", "name": "EXPIRY",   "value": "250131",         "offset": 16 },
    { "ai": "10", "name": "BATCH/LOT","value": "ABC123",         "offset": 24 },
    { "ai": "21", "name": "SERIAL",   "value": "SN0001",         "offset": 33 }
  ]
}
```

## 使用示例（GS1 Digital Link URI）

`data` 以 `http://` 或 `https://` 开头时，按 GS1 Digital Link URI 解析：

- 路径部分必须是由 `/` 分隔的 AI/值 对（如 `/01/09501101530003/10/ABC123`）；
  落单的段返回 `INVALID_URI`。
- 查询参数携带其余 AI（如 `?17=250131&21=SN0001`）；非数字的查询参数
  （如 `linkType`）会被忽略，数字但不支持的 AI 返回 `UNSUPPORTED_AI`。
- 值先做百分号 UTF-8 解码（`+` 按字面保留），再执行与元素串完全相同的
  字符集、长度、校验位与日期校验。
- 每个 AI 在整个 URI 中只允许出现一次；重复出现（包括同一 AI 同时出现在
  路径和查询参数中）返回 `DUPLICATE_AI`。
- 响应元素按"路径顺序在前、查询参数顺序在后"排列；`offset` 与错误中的
  `offset` 均为原始 URI 中的字节偏移。

请求：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.example.com/01/09501101530003/10/ABC123?17=250131&21=SN0001"}'
```

响应 `200 OK`，路径中的 01、10 在前，查询参数中的 17、21 在后：

```json
{
  "elements": [
    { "ai": "01", "name": "GTIN",      "value": "09501101530003", "offset": 23 },
    { "ai": "10", "name": "BATCH/LOT", "value": "ABC123",         "offset": 41 },
    { "ai": "17", "name": "EXPIRY",    "value": "250131",         "offset": 51 },
    { "ai": "21", "name": "SERIAL",    "value": "SN0001",         "offset": 61 }
  ]
}
```

同一 AI 同时出现在路径和查询参数中时返回 `422`：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.example.com/01/09501101530003?01=09501101530003"}'
```

```json
{
  "detail": {
    "error": "DUPLICATE_AI",
    "message": "Application identifier '01' appears in both the URI path and query string",
    "offset": 41,
    "expected": "each AI to appear only once in the URI",
    "found": "01"
  }
}
```

## 错误响应

解析或校验失败返回 `422 Unprocessable Entity`，`detail` 中包含错误码、
字节偏移（`offset`，UTF-8 字节序）与预期规则（`expected`）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "0109501101530004"}'
```

```json
{
  "detail": {
    "error": "INVALID_CHECK_DIGIT",
    "message": "GTIN check digit mismatch: expected 3, found 4",
    "offset": 15,
    "expected": "Mod-10 check digit 3",
    "found": "4"
  }
}
```

错误码一览：

| 错误码                 | 说明                                     |
| ---------------------- | ---------------------------------------- |
| `EMPTY_INPUT`          | 输入为空                                 |
| `UNEXPECTED_SEPARATOR` | 在应出现 AI 的位置遇到分隔符             |
| `INVALID_AI`           | 不是合法的 2 位数字 AI                   |
| `UNSUPPORTED_AI`       | 不支持的 AI（仅支持 01/10/17/21）        |
| `FIELD_TOO_SHORT`      | 固定长度字段字符不足                     |
| `FIELD_EMPTY`          | 可变长度字段为空                         |
| `FIELD_TOO_LONG`       | 可变长度字段超过 20 字符                 |
| `INVALID_CHARACTER`    | 字符不符合该字段字符集                   |
| `INVALID_CHECK_DIGIT`  | GTIN Mod-10 校验位不匹配                 |
| `INVALID_DATE`         | 效期不是真实日期                         |
| `INVALID_URI`          | Digital Link URI 路径未按 AI/值 成对出现 |
| `DUPLICATE_AI`         | 同一 AI 在 URI 中重复出现（含路径与查询参数同时出现） |
| `INVALID_PERCENT_ENCODING` | URI 中的百分号编码不是合法的 UTF-8   |

## 本地开发

```bash
pip install -r requirements-dev.txt
pytest                 # 运行测试
uvicorn app.main:app --reload --port 8080
```

## 项目结构

```
app/
  main.py   # FastAPI 应用与请求/响应模型
  gs1.py    # GS1 元素串 / Digital Link URI 解析与校验核心
tests/
  test_parse.py
Dockerfile
docker-compose.yml
requirements.txt      # 运行依赖
requirements-dev.txt  # 测试依赖
```
