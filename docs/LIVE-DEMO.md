# SAIV 本机 Live Demo

本次已准备独立演示数据库 `saiv_demo`。学生、教师网页使用正常权限模式
（`PUBLIC_TEST_MODE=false`），与之前的公开测试数据库分开。

## 网页与账号

| 用途 | 地址 | 账号 |
| --- | --- | --- |
| 学生网页 | http://localhost:3000 | `student.demo@example.com` |
| 教师 Dashboard | http://localhost:8501 | `instructor.demo@example.com` |
| 管理员 Dashboard / 审计 | http://localhost:8501 | `admin.demo@example.com` |
| 后端 API 文档 | http://localhost:8000/docs | 使用相应角色登录 |
| 人脸服务 API | http://localhost:58001/docs | 本机服务 |
| Grafana | http://localhost:3001 | `admin` |
| Prometheus | http://localhost:9090 | 本机服务 |

这些本机演示账号的密码统一为 **`DemoPass123!`**。
另外准备了 `lockout.demo@example.com`，用于单独展示连续错误密码锁定，
避免锁住学生主账号。Grafana 使用自己的登录，与系统账号分开。

## 建议演示流程（约 5 分钟）

1. 同时打开学生端和教师端两个浏览器标签页，分别登录学生和教师。
2. 教师展示 Overview：已有 3 个场次，其中 1 个开放；历史示例显示统计、
   审核队列和图表。`DEMO-HISTORY` 及其场次明确标记为 seeded 样例，
   这些不是本次实际采集的人脸或真实签到结果。
3. 学生打开 **Face Enrollment**，本人允许摄像头并完成录脸。
   系统尚未替你采集人脸，演示前应本人排练此步骤。
4. 学生打开 **Check In → Live Check-in Demonstration**，按屏幕提示完成
   活体挑战，允许定位，提交签到。人脸、活体、设备和地理校验均实际执行。
5. 教师点击 **REFRESH DATA** 或查看 **CHECKINS**，展示新增记录、状态及
   风险信号。需要人工审核的记录进入 review 流程；可展示 CSV 导出。
6. 管理员账号可另开教师端查看 **SYS LOGS - AUDIT**；教师无管理员审计权限。
   最后可打开 Grafana 展示请求量、延迟和状态。

## 演示前必须核对

- 使用本机 Chrome 的 `localhost` 网页，摄像头和定位权限由本人确认。
- 当前签到场地默认 **NTU Campus，纬度 1.3483、经度 103.6831，半径 100 米**。
  如在其他新加坡地点演示，请先在教师端的场次 Settings 修改场地坐标。
  不在围栏内会被标记或拒绝；新加坡境外 GPS 必须拒绝，这是正常安全行为。
- Live 场次当前开放 24 小时。演示当天检查时间窗口，必要时在教师端调整。
- 同一学生每个场次只能签到一次。排练成功后，正式展示应新建并激活一个场次，
  人脸注册可以保留。不要为重复演示绕过重复签到或安全校验。
- Overview 的样例历史记录可作讲解备用；不能把它们当成本次真实识别结果。
- Grafana 的请求速率图需要流量和采样时间，启动后少量空白数据属正常情况。

## 重启本机演示

打开 Docker Desktop，等待引擎就绪，在项目根目录执行：

```bash
docker compose -p saiv-clarifications-verify -f .demo/compose.yml up -d demo-backend frontend dashboard prometheus grafana
```

账号、课程和记录保存在 Docker 的 PostgreSQL 数据卷中；不要删除卷。
如果仅需要重新打开默认 Live 场次的时间窗口（不删除已有签到）：

```bash
docker compose -p saiv-clarifications-verify -f .demo/compose.yml exec -T demo-backend python - < .demo/seed_demo.py
```

本机配置和重置脚本在被 Git 忽略的 `.demo/` 中，包含本机演示密钥，不随代码提交。
这些配置为本台电脑生成；换电脑需要重新准备本机配置和镜像。

## 本次已验证

- 学生端生产构建成功，Chrome 登录后能看到开放的演示场次和人脸注册入口。
- 教师端 Chrome 登录成功，Overview 实际显示统计、图表和审核队列。
- 学生、教师、管理员登录及权限边界通过 API 验证。
- 学生网页、Dashboard、后端、人脸、Prometheus、Grafana 健康/页面检查均返回 200。
- 未替本人授予摄像头/定位权限，也未声称已完成本人现场人脸签到。
