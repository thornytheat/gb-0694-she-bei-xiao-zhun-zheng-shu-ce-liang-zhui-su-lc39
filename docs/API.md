# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m7`：独立运行到指定模块，JSON结构同运行接口。

`GET /api/history`：历史批次。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

结构错误422，版本变化409，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。

## M8 台账发布（版本化设备台账）

`POST /api/m8`：无状态试算，不落库。JSON可含`csv`、`as_of`(YYYY-MM-DD，缺省取当天)、`previous`(上版条目数组)，或multipart字段`file`。

`POST /api/ledger/publish`：发布全量批次为新台账版本；同内容（SHA256+规则版本+判定日）重跑只复用/恢复既有版本，不新增版本号。`GET /api/ledger/current`读取当前指针版本，`GET /api/ledger/versions`列出版本，`GET /api/ledger/versions/<n>`读取指定版本。

`POST /api/ledger/rollback`接收`version`，把当前指针恢复到旧版（历史版本不删除；重跑旧批次仅移回指针）。`POST /api/ledger/review`接收`version`、`revision`、`decision`，revision乐观锁，陈旧提交409。`GET /api/ledger/audit`返回发布/幂等/回滚/复核审计轨迹，`GET /api/ledger/evidence.csv?version=n`导出含分桶、变化来源与隔离行的证据CSV。

状态四桶：ACTIVE在册、MISSING本批未出现待核实（不下架）、REVOKED撤销（跨批粘性）、EXPIRED过期（半开区间`valid_to<=as_of`）。发布整体在单事务内完成，失败返回500/PUBLISH且旧版指针不变。
