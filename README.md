# 易校园电费监控

`astrbot_plugin_electricity_monitor` 是面向 AstrBot 4.24.2+ 的多会话、多寝室电费监控插件。它通过易校园现有电费接口查询寝室剩余量，并在达到低电量阈值时主动提醒。

## 功能

- Dashboard 写入并验证全局 `shiroJID`；`ymId` 可选，接口只返回掩码状态。
- 成功请求会自动接收服务端 `Set-Cookie` 中滚动更新的 `shiroJID` 并持久化。
- 已有订阅时优先通过实际寝室查询验证登录态，避免 `queryArea` 单独返回 204 造成误判。
- 电量优先读取 `data.surplus`，没有该字段时再兼容旧响应的 `data.amount`。
- 当响应同时包含 `data.surplus` 和 `data.amount` 时，`surplus` 作为电量，`amount` 作为余额附加展示。
- 支持校区、楼栋、楼层、房间四级联动，也可直接填写房间接口参数。
- 每个私聊或群聊可以订阅多个寝室，分别设置别名、单位、阈值和查询频率。
- 相同寝室被多个会话订阅时，同一轮只请求一次。
- 首次达到或低于阈值时提醒；恢复到阈值以上后重新布防。
- 登录态过期后暂停查询，并向指定管理员私聊通知一次。
- SQLite 保存会话、订阅、运行状态和最近 30 天采样。
- Dashboard 展示最新电量、错误诊断和 30 天趋势。
- 新增或修改订阅后立即执行首次查询，无需等待后台调度。
- Dashboard 可将已有寝室订阅复制到另一个私聊或群聊会话。
- Dashboard 默认打开最低电量订阅所在会话，并按低电量优先排序。
- Dashboard UI 改为卡片化仪表盘布局，统计、列表和编辑区更清晰。
- Dashboard v1.3.0 使用 Tailwind CSS 与 shadcn 风格组件重构管理页，统一卡片、表单、表格、标签和诊断样式。
- 会话导入会规范化 AstrBot 旧式嵌套 UMO，避免把群号误识别为私聊。
- 校区级联使用 `queryArea.rows[].id` 查询楼栋，兼容同时返回不同 `areaId` 的部署。

## 安装

1. 将整个 `astrbot_plugin_electricity_monitor` 目录放入 AstrBot 插件目录。
2. 安装 `requirements.txt` 中的依赖并重载插件。
3. 先让机器人在目标私聊或群聊中收到一条消息，或在管理页点击“导入已有会话”。
4. 在插件管理页写入自己的 `shiroJID`；抓包请求中确有 `ymId` 时再填写它。
5. 逐级选择寝室，或切换到“使用手动参数”填写四个定位代码后保存订阅。

凭据来自用户自己的合法易校园登录会话。插件不提供自动登录、抓包、验证码绕过或证书校验绕过能力。

`shiroJID` 输入框可粘贴纯值、`shiroJID=...` 或完整 Cookie 文本；插件只会
提取并保存 `shiroJID`。接口返回 `success=true` 时，如果响应包含新的
`shiroJID`，插件会自动续存并用于下一次查询。`204 请重新登录` 等失败响应
附带的匿名 Cookie 会被忽略，不会覆盖最后一次有效登录态。

`ymId` 与 `areaId` 不是同一个参数。若 `queryRoomSurplus` 请求没有携带
`ymId`，留空即可。手动房间参数示例：

```text
areaId=2510120172541411338
buildingCode=39
floorCode=71
roomCode=12598
platform=YUNMA_APP
```

`platform` 由插件固定为 `YUNMA_APP`，无需在页面填写。仅保存
`shiroJID` 时，若校区接口无法验证，页面会保持“待验证”；保存寝室后点击
“立即查询”，成功取得电量即会把登录态标记为有效。

## 命令

```text
/电费
/电费 查询 [寝室别名]
/电费 状态
/电费 监控 开|关 寝室别名
/电费 阈值 寝室别名 数值
/电费 频率 寝室别名 分钟
```

私聊用户可以管理自己的订阅；群聊仅群主、群管理员或 AstrBot 管理员可以修改。

## 默认行为

- 默认阈值：`20`
- 默认单位：`度`
- 默认查询频率：15 分钟
- 单订阅允许范围：5–1440 分钟
- 易校园请求全局串行，最小间隔 2 秒
- 历史数据保留 30 天

接口当前使用：

```text
https://application.xiaofubao.com/app/electric/queryArea
https://application.xiaofubao.com/app/electric/queryBuilding
https://application.xiaofubao.com/app/electric/queryFloor
https://application.xiaofubao.com/app/electric/queryRoom
https://application.xiaofubao.com/app/electric/queryRoomSurplus
```

易校园接口并非公开稳定 API。若字段或路径发生变化，插件会停止该轮查询并在诊断页记录错误，不会伪造电量。

## 开发检查

```bash
python -m unittest discover -s tests -v
python -m compileall -q main.py core tests
node --check pages/electricity-monitor/app.js
```
