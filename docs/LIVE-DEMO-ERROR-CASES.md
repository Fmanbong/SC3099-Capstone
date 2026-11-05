# Live demo 错误输入实测

日期：2026-11-05（新加坡）。环境：本机演示后端 http://localhost:8000，正常权限模式；真实 PostgreSQL、Redis 与人脸服务。

结果：**28/28 项检查符合预期**。密码错误的前9次逐次验证；下表合并为一行，因此列出20类场景。

| 错误输入 / 场景 | 预期 HTTP | 实际 HTTP | 实际错误 code |
| --- | --- | --- | --- |
| 错误密码（第1–9次） | 401 | 401 | `INVALID_CREDENTIALS` |
| 第10次密码错误，锁定账号 | 429 | 429 | `ACCOUNT_LOCKED` |
| 锁定后使用正确密码 | 429 | 429 | `ACCOUNT_LOCKED` |
| 不存在的账号 | 401 | 401 | `INVALID_CREDENTIALS` |
| 未携带登录凭证 | 401 | 401 | `AUTH_REQUIRED` |
| 伪造 JWT | 401 | 401 | `INVALID_TOKEN` |
| 学生访问管理员审计日志 | 403 | 403 | `FORBIDDEN` |
| 创建开始时间在过去的场次 | 400 | 400 | `INVALID_SESSION_WINDOW` |
| 非法纬度 latitude=91 | 422 | 422 | `VALIDATION_ERROR` |
| 新山 GPS（新加坡境外） | 403 | 403 | `GPS_OUTSIDE_SINGAPORE` |
| X-Forwarded-For: 8.8.8.8, 127.0.0.1 | 403 | 403 | `IP_OUTSIDE_SINGAPORE` |
| 私有 IP 配合境外 GPS | 403 | 403 | `GPS_OUTSIDE_SINGAPORE` |
| 错误格式的转发 IP | 403 | 403 | `CLIENT_IP_INVALID` |
| 没有定位同意 | 400 | 400 | `GEOLOCATION_CONSENT_REQUIRED` |
| 没有摄像头同意即提交录脸 | 400 | 400 | `CAMERA_CONSENT_REQUIRED` |
| 非法 Base64 图像 | 400 | 400 | `FACE_IMAGE_INVALID` |
| 无脸图片（生成的纯色图） | 400 | 400 | `FACE_IMAGE_INVALID` |
| 已关闭的场次 | 400 | 400 | `SESSION_NOT_ACTIVE` |
| 不存在的场次 UUID | 404 | 404 | `SESSION_NOT_FOUND` |
| 重复签到 | 400 | 400 | `CHECKIN_EXISTS` |

## 429 的实际输出

浏览器已打开登录接口的 Swagger 表单，并实际执行得到 HTTP 429。响应正文包含：

```json
{
  "detail": "Account is blocked after repeated incorrect passwords",
  "code": "ACCOUNT_LOCKED"
}
```

实际响应还包含用于日志关联的 `request_id`。Swagger 的 `Undocumented` 标签表示 OpenAPI 元数据尚未列出该错误响应，不影响服务器实际返回的 429。

## 浏览器演示入口

- 学生端：http://localhost:3000
- 教师端：http://localhost:8501
- 登录接口：http://localhost:8000/docs#/Authentication/login_api_v1_auth_login_post

这轮测试创建独立测试账号、管理员拥有的验证课程和场次。学生/教师主演示账号保持可用，教师分配课程的 Overview 不会混入这些验证场次。

当前 Swagger 中使用的已锁定测试账号：`negative-lockout-99cff178@example.com`，正确演示密码为 `DemoPass123!`。重复执行仍会返回 429，符合锁定规则。

## 本机重复测试

本机被 Git 忽略的 `.demo/check_error_cases.py` 每次使用新账号和新场次，重跑完整流程不会锁住主演示账号。依赖为 httpx 和 Pillow。

```bash
/private/tmp/saiv-clarifications-venv/bin/python .demo/check_error_cases.py
```

详细结果保存在 `.demo/error-results.json`；脚本与本机账号数据不随 Git 提交。
