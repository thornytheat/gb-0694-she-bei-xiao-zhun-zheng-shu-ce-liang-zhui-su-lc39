from pathlib import Path
RULE_VERSION = "2026.11.19.1"
TITLE = '设备校准证书与测量追溯'
SHORT = 'calibration-chain'
MODULES = ['证书接收', '设备别名', '有效区间', '量程匹配', '溯源链路', '测量裁决', '撤销复核', '台账发布']
HEADERS = ['cert', 'device', 'alias', 'parent', 'valid_from', 'valid_to', 'minimum', 'maximum', 'unit', 'revoked', 'measured_at', 'value', 'measure_unit', 'version']
SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "operations.csv"
LEDGER_SAMPLE_W1 = Path(__file__).resolve().parent.parent / "sample_data" / "ledger-week1.csv"
LEDGER_SAMPLE_W2 = Path(__file__).resolve().parent.parent / "sample_data" / "ledger-week2.csv"
LEDGER_SAMPLE_W3 = Path(__file__).resolve().parent.parent / "sample_data" / "ledger-week3.csv"
LIMIT = 10000
