REASONS = {"COLUMNS":"列数不符", "HEADER":"字段缺失", "MISSING":"必填值为空", "NUMBER":"数值无效", "RANGE":"超出范围", "DUPLICATE":"相同行重复", "CONFLICT":"同键数值冲突", "VERSION":"版本冲突", "EMPTY":"输入为空", "STRUCTURE":"文件结构错误", "REVOKED_FLAG":"撤销标识无效", "OVERLAP":"同机证书区间重叠", "NOT_YET_VALID":"证书尚未生效", "PUBLISH":"台账发布失败", "DATE":"日期格式无效", "UNIT":"单位无效"}
def isolate(ctx, row, code, detail):
    ctx['quarantine'].append({**row, '原因码': code, '原因': detail})
