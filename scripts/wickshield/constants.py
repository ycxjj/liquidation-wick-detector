"""
WickShield v5.1 与设计文档对齐的共享参数（docs/WickShield_Complete_Design_v5.md）
"""
from decimal import Decimal

# 精算：币种基础日费率 (bps) — 仍可用于其他模块或对照
BASE_RATES_BPS = {
    "BTC/USDT": 50,
    "ETH/USDT": 80,
    "SOL/USDT": 150,
    "LAB/USDT": 80,
    "DEFAULT": 300,
}

# 保费引擎 v3.0：基础日费率以「百分数」存储，例如 BTC=0.05 表示 0.05%/日，LAB=1.0 表示 1%/日（再经杠杆等乘数）
BASE_RATES_DAILY_PCT: dict[str, Decimal] = {
    "BTC/USDT": Decimal("0.05"),
    "ETH/USDT": Decimal("0.08"),
    "SOL/USDT": Decimal("0.15"),
    "TON/USDT": Decimal("0.20"),
    "DOGE/USDT": Decimal("0.50"),
    "LAB/USDT": Decimal("0.8"),  # 高风险币：0.8%/日，极端行情由动态封顶约束
    "SUI/USDT": Decimal("0.30"),
    "APT/USDT": Decimal("0.30"),
    "ARB/USDT": Decimal("0.30"),
    "OP/USDT": Decimal("0.30"),
    "PEPE/USDT": Decimal("1.00"),
    "WIF/USDT": Decimal("0.80"),
    "BONK/USDT": Decimal("1.20"),
    "SEI/USDT": Decimal("0.40"),
    "INJ/USDT": Decimal("0.40"),
    "TIA/USDT": Decimal("0.50"),
    "RUNE/USDT": Decimal("0.50"),
    "ORDI/USDT": Decimal("0.60"),
    "DEFAULT": Decimal("0.30"),
}

# 赔付：振幅触发阈值 (1% = 100 pips)
THRESHOLDS_PIPS = {
    "BTC/USDT": 150,
    "ETH/USDT": 200,
    "SOL/USDT": 300,
    "LAB/USDT": 500,
    "DEFAULT": 300,
}

MIN_DAILY_RATE = Decimal("0.0005")  # 最低日费率（小数）：0.05%/日
SAFETY_FACTOR = Decimal("1.5")

# --- 保费 v3.0：动态封顶（三层联动 + 硬性边界）---
PREMIUM_V3_BASE_CAP_RATIO = Decimal("0.50")  # 基准封顶：本金 × 该比例再经动态调整
PREMIUM_V3_CAP_ABS_MIN = Decimal("0.10")  # 封顶比例下限（仍为本金比例）
PREMIUM_V3_CAP_ABS_MAX = Decimal("0.75")  # 封顶比例上限

# 偿付能力比率阈值 (%) — 文档 §4.2
SOLVENCY_HEALTHY = Decimal("50")
SOLVENCY_WATCH = Decimal("30")
SOLVENCY_WARNING = Decimal("20")
SOLVENCY_DANGER = Decimal("10")

# 动态投保/赔付上限 (USDT) — 文档 §9.1
COVERAGE_LIMITS = {
    "healthy": {"single": Decimal("500"), "daily_24h": Decimal("5000")},
    "watch": {"single": Decimal("300"), "daily_24h": Decimal("3000")},
    "warning": {"single": Decimal("100"), "daily_24h": Decimal("1000")},
    "danger": {"single": Decimal("100"), "daily_24h": Decimal("1000")},
    "emergency": {"single": Decimal("0"), "daily_24h": Decimal("0")},
}

# 资金池紧张时的保费乘数 — 文档 §2 + §4.2 动态定价
SOLVENCY_PREMIUM_MULTIPLIERS = {
    "healthy": Decimal("1.0"),
    "watch": Decimal("1.2"),
    "warning": Decimal("1.5"),
    "danger": Decimal("2.0"),
    "emergency": Decimal("2.5"),
}

# 风控动作 — 文档 §4.2
RISK_ACTIONS = {
    "healthy": {"block_new": False, "payout_ratio": Decimal("1.0"), "pause_new_symbols": False},
    "watch": {"block_new": False, "payout_ratio": Decimal("1.0"), "pause_new_symbols": True},
    "warning": {"block_new": True, "payout_ratio": Decimal("1.0"), "pause_new_symbols": True},
    "danger": {"block_new": True, "payout_ratio": Decimal("0.5"), "pause_new_symbols": True},
    "emergency": {"block_new": True, "payout_ratio": Decimal("0.0"), "pause_new_symbols": True},
}

# 产品线保费系数 — 文档 §15
PRODUCT_TIER_MULTIPLIERS = {
    "basic": Decimal("1.0"),
    "enhanced": Decimal("1.5"),
    "ultimate": Decimal("2.5"),
}

# A 档内定价系数分档（有效日费率÷冷启动底价，再叠波动/插针；见 report_risk.compute_pricing_coefficient）
RISK_SUBGRADE_COEFF_A_MINUS_MAX = Decimal("5")
RISK_SUBGRADE_COEFF_A_MAX = Decimal("10")
RISK_SUBGRADE_COEFF_A_PLUS_MAX = Decimal("25")

# A-/A/A+ 相对 A- 的保费乘子（在基础日费率之上叠加）
RISK_SUBGRADE_PREMIUM_MULT = {
    "A-": Decimal("1.00"),
    "A": Decimal("1.12"),
    "A+": Decimal("1.35"),
}

# 当月已批准赔付笔数 → 动态附加费乘子（笔数越多费率越高）
MONTHLY_PAYOUT_SURCHARGE_STEPS: list[tuple[int, Decimal]] = [
    (0, Decimal("1.00")),
    (1, Decimal("1.08")),
    (3, Decimal("1.15")),
    (5, Decimal("1.25")),
    (8, Decimal("1.35")),
    (12, Decimal("1.50")),
    (20, Decimal("1.70")),
]

# 压力测试场景 — 文档 §5.1
STRESS_SCENARIOS = {
    "mild": {"lambda": 3, "avg_loss": 50, "label": "场景A-常规日"},
    "moderate": {"lambda": 8, "avg_loss": 80, "label": "场景B-波动日"},
    "severe": {"lambda": 15, "avg_loss": 120, "label": "场景C-极端日"},
    "black_swan": {"lambda": 5, "avg_loss": 500, "label": "场景D-灾难周(单次模拟)"},
}
