# 示例接口文档（Atlas `atlas gen` 上手样例）

> **这是随仓的**虚构**样例输入**，只为让你零配置跑一遍「接口文档 → flow YAML + 契约候选 +
> pytest 回归代码包」。它不是任何真实系统，接口/字段名都是占位。
> 换成你自己的文档（Markdown / OpenAPI 3 / Swagger 2 / 纯文本，按内容嗅探）即可。

## GET /web/example/customer/{code}

按客户编码查询客户详情（`code` 是路径参数，直接写在路由里）。

**响应 200**

```json
{
  "resultCode": "BUG-000",
  "data": {
    "customerCode": "CUST-EXAMPLE-0001",
    "name": "示例客户",
    "status": "ACTIVE"
  }
}
```

## POST /web/example/order/query

按条件查询订单列表。

**请求体**

```json
{
  "customerCode": "CUST-EXAMPLE-0001",
  "pageSize": 20,
  "pageNo": 1
}
```

**响应 200**

```json
{
  "resultCode": "BUG-000",
  "data": {
    "total": 1,
    "rows": [
      {"orderNo": "SO-EXAMPLE-0001", "status": "OPEN"}
    ]
  }
}
```
