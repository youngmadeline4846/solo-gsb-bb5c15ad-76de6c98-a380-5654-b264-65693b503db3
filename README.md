# GS1 条码元素串解析 API

基于 Python + FastAPI 的 GS1 元素串（Element String）解析服务。接收扫码枪原始输出
（含 ASCII 29 / FNC1 分隔符），返回保持原顺序的结构化元素，并完成字符集、长度、
GTIN Mod-10 校验位与真实日期校验。

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

## 使用示例

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
  gs1.py    # GS1 元素串解析与校验核心
tests/
  test_parse.py
Dockerfile
docker-compose.yml
requirements.txt      # 运行依赖
requirements-dev.txt  # 测试依赖
```
