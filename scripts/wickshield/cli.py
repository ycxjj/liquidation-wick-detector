#!/usr/bin/env python3
"""
WickShield v5.1 统一命令行入口

用法:
  python -m scripts.wickshield.cli premium --amount 1000 --symbol LAB/USDT ...
  python -m scripts.wickshield.cli payout --coverage 1000 ...
  python -m scripts.wickshield.cli solvency check --pool 50000 --coverage 100000
  python -m scripts.wickshield.cli solvency stress --scenario severe --runs 100
  python -m scripts.wickshield.cli backtest --pool 50000 --coverage 100000
  python -m scripts.wickshield.cli live --symbol LAB/USDT --amount 1000 --exchange gate
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Optional

from .backtest_runner import print_backtest_report, run_full_backtest
from .payout_calc import apply_haircut, calc_payout
from .premium_calc import calc_premium
from .solvency_check import SolvencyRiskManager


def _emit(result: Dict[str, Any], args: argparse.Namespace) -> int:
    if getattr(args, "compact", False):
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success", True) else 1


def _add_json_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--compact",
        action="store_true",
        help="输出单行 JSON（便于管道处理）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wickshield",
        description="WickShield v5.1 精算 CLI（保费 / 赔付 / 偿付能力 / 回测）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- premium ---
    p_premium = sub.add_parser("premium", help="计算保费")
    _add_json_flags(p_premium)
    p_premium.add_argument("--amount", type=float, required=True, help="投保金额 (USDT)")
    p_premium.add_argument("--symbol", type=str, default="BTC/USDT", help="交易对，如 BTC/USDT")
    p_premium.add_argument("--days", type=int, default=7, help="保障天数")
    p_premium.add_argument("--leverage", type=int, default=1, help="杠杆倍数")
    p_premium.add_argument("--credit-score", type=int, default=300, dest="credit_score", help="信用分")
    p_premium.add_argument(
        "--solvency-ratio",
        type=float,
        default=60.0,
        dest="solvency_ratio",
        help="偿付能力比率 %%（用于动态保费）",
    )
    p_premium.add_argument(
        "--tier",
        type=str,
        choices=["basic", "enhanced", "ultimate"],
        default="basic",
        dest="product_tier",
        help="产品线: basic / enhanced / ultimate",
    )
    p_premium.add_argument(
        "--large-policy",
        action="store_true",
        dest="large_policy",
        help="大额保单（不享受信用折扣）",
    )
    p_premium.add_argument(
        "--max-premium-ratio",
        type=float,
        default=None,
        dest="max_premium_ratio",
        help="强制固定封顶比例 (0~1)，覆盖动态封顶；默认走 v3 动态封顶",
    )
    p_premium.add_argument(
        "--atr-current",
        type=float,
        default=None,
        dest="current_atr",
        help="当前 ATR（与 --atr-base 同时给出时参与封顶调节）",
    )
    p_premium.add_argument(
        "--atr-base",
        type=float,
        default=None,
        dest="base_atr",
        help="基准 ATR",
    )
    p_premium.add_argument(
        "--spike-count-1h",
        type=int,
        default=0,
        dest="spike_count_1h",
        help="近 1 小时插针次数（>=3 触发封顶冷静期档）",
    )
    p_premium.add_argument(
        "--no-reports",
        action="store_true",
        dest="no_report_risk",
        help="禁用日报/费率表，回退 legacy 静态底价表",
    )
    p_premium.add_argument(
        "--report-days",
        type=int,
        default=30,
        dest="report_days",
        help="日报回溯天数（与 --use-reports 联用）",
    )
    p_premium.add_argument(
        "--monthly-payout-count",
        type=int,
        default=None,
        dest="monthly_approved_payouts",
        help="覆盖当月已批准赔付笔数（默认从 claims_log 统计）",
    )

    # --- reports (日报 → 风险画像) ---
    p_reports = sub.add_parser("reports", help="日报历史风险画像（数据驱动定价输入）")
    rep_sub = p_reports.add_subparsers(dest="rep_command", required=True)
    p_rep_profile = rep_sub.add_parser("profile", help="单币种风险画像与建议费率乘数")
    _add_json_flags(p_rep_profile)
    p_rep_profile.add_argument("--symbol", type=str, required=True)
    p_rep_profile.add_argument("--days", type=int, default=30, help="回溯日历天数")
    p_rep_profile.add_argument(
        "--with-premium",
        action="store_true",
        help="附带按画像计算的保费示例（需 --amount 等）",
    )
    p_rep_profile.add_argument("--amount", type=float, default=1000.0)
    p_rep_profile.add_argument("--policy-days", type=int, default=7, dest="policy_days")
    p_rep_profile.add_argument("--leverage", type=int, default=10)
    p_rep_profile.add_argument("--solvency-ratio", type=float, default=50.0, dest="solvency_ratio")

    p_rep_refresh = rep_sub.add_parser("refresh", help="从近 N 天日报重建 data/rates/dynamic_rates.json")
    _add_json_flags(p_rep_refresh)
    p_rep_refresh.add_argument("--days", type=int, default=30)

    p_payout = sub.add_parser("payout", help="计算赔付")
    _add_json_flags(p_payout)
    p_payout.add_argument("--coverage", type=float, required=True, help="保额 (USDT)")
    p_payout.add_argument("--symbol", type=str, default="BTC/USDT", help="交易对")
    p_payout.add_argument(
        "--amplitude",
        type=float,
        required=True,
        dest="actual_amplitude",
        help="实际插针振幅 %%",
    )
    p_payout.add_argument(
        "--global-payout-today",
        type=float,
        default=0.0,
        dest="global_payout_today",
        help="该币种今日已赔付总额 (USDT)",
    )
    p_payout.add_argument(
        "--solvency-ratio",
        type=float,
        default=60.0,
        dest="solvency_ratio",
        help="偿付能力比率 %%",
    )
    p_payout.add_argument(
        "--solvency-payout-ratio",
        type=float,
        default=None,
        dest="solvency_payout_ratio",
        help="强制指定赔付比例 (0-1)，默认按偿付状态自动取值",
    )
    p_haircut = sub.add_parser("haircut", help="有序清算 Haircut 分配")
    _add_json_flags(p_haircut)
    p_haircut.add_argument(
        "--pending",
        type=float,
        nargs="+",
        required=True,
        help="待赔付金额列表，如 --pending 500 300 200",
    )
    p_haircut.add_argument("--pool", type=float, required=True, help="可用于赔付的剩余资金")

    # --- solvency ---
    p_sol = sub.add_parser("solvency", help="偿付能力检查与压力测试")
    sol_sub = p_sol.add_subparsers(dest="sol_command", required=True)

    p_check = sol_sub.add_parser("check", help="当前风险状态")
    _add_json_flags(p_check)
    p_check.add_argument("--pool", type=float, required=True, help="资金池总额 (USDT)")
    p_check.add_argument("--coverage", type=float, required=True, help="总有效保额 (USDT)")

    p_stress = sol_sub.add_parser("stress", help="蒙特卡洛压力测试")
    _add_json_flags(p_stress)
    p_stress.add_argument("--pool", type=float, required=True)
    p_stress.add_argument("--coverage", type=float, required=True)
    p_stress.add_argument(
        "--scenario",
        type=str,
        choices=["mild", "moderate", "severe", "black_swan", "all"],
        default="severe",
        help="压力场景: mild/moderate/severe/black_swan，或 all 跑全部",
    )
    p_stress.add_argument("--runs", type=int, default=100, help="模拟次数")
    p_stress.add_argument("--seed", type=int, default=42, help="随机种子（可复现）")

    # --- backtest ---
    p_bt = sub.add_parser("backtest", help="全链路回测（文本输出）")
    p_bt.add_argument("--pool", type=float, default=50_000.0, help="资金池规模")
    p_bt.add_argument("--coverage", type=float, default=100_000.0, help="总保额")
    p_bt.add_argument("--symbol", type=str, default="LAB/USDT", help="回测币种")
    p_bt.add_argument("--amount", type=float, default=1000.0, help="单笔投保/保额")
    p_bt.add_argument("--days", type=int, default=7)
    p_bt.add_argument("--leverage", type=int, default=10)
    p_bt.add_argument("--amplitude", type=float, default=6.0, help="模拟插针振幅 %%")
    p_bt.add_argument(
        "--global-payout-today",
        type=float,
        default=4500.0,
        dest="global_payout_today",
        help="今日已赔付（测全局限额）",
    )
    p_bt.add_argument("--json", action="store_true", help="以 JSON 输出回测摘要")
    _add_json_flags(p_bt)

    # --- live (交易所实时数据) ---
    p_live = sub.add_parser("live", help="拉取交易所实时 K 线并精算（ATR / 插针 / 保费）")
    _add_json_flags(p_live)
    p_live.add_argument(
        "--mode",
        choices=["quote", "premium", "full"],
        default="full",
        help="quote=仅行情; premium=行情+保费; full=行情+保费+赔付试算(+可选偿付能力)",
    )
    p_live.add_argument(
        "--exchange",
        type=str,
        default=None,
        help="交易所 binanceusdm/okx/gate/bybit 等；省略则按 币安→欧易→Gate→Bybit 回退",
    )
    p_live.add_argument("--symbol", type=str, default="LAB/USDT")
    p_live.add_argument("--timeframe", type=str, default="5m", help="K 线周期")
    p_live.add_argument("--days-back", type=float, default=2.0, dest="days_back", help="回溯天数（算基准 ATR）")
    p_live.add_argument("--amount", type=float, default=1000.0)
    p_live.add_argument("--days", type=int, default=7, help="保单天数")
    p_live.add_argument("--leverage", type=int, default=10)
    p_live.add_argument("--credit-score", type=int, default=300, dest="credit_score")
    p_live.add_argument("--pool", type=float, default=None, help="资金池（与 --coverage 同给则自动算偿付比率）")
    p_live.add_argument("--coverage", type=float, default=None, help="总有效保额")
    p_live.add_argument(
        "--solvency-ratio",
        type=float,
        default=None,
        dest="solvency_ratio",
        help="偿付能力%%；未给且提供 pool/coverage 时自动计算",
    )
    p_live.add_argument("--global-payout-today", type=float, default=0.0, dest="global_payout_today")
    p_live.add_argument("--tier", type=str, choices=["basic", "enhanced", "ultimate"], default="basic", dest="product_tier")
    p_live.add_argument("--no-reports", action="store_true", dest="no_report_risk")
    p_live.add_argument("--report-days", type=int, default=30, dest="report_days")

    p_mon = sub.add_parser("monitor", help="实时理赔监控（定时任务用）")
    _add_json_flags(p_mon)
    p_mon.add_argument(
        "--dry-run",
        action="store_true",
        help="只检测不扣减 global_payout_today、不写链上",
    )

    p_reset = sub.add_parser(
        "reset",
        help="日切额度重置（建议每天 00:00 crontab）",
    )
    _add_json_flags(p_reset)

    p_sym = sub.add_parser("symbols", help="热门币单（六所成交额 Top N）")
    _add_json_flags(p_sym)
    p_sym_sub = p_sym.add_subparsers(dest="sym_command", required=True)
    p_sym_top = p_sym_sub.add_parser(
        "refresh",
        help="从六所拉最新热门 USDT 永续并写入 data/wickshield/monitor_symbols.json",
    )
    p_sym_top.add_argument("--limit", type=int, default=50, help="保留前 N 个（默认 50）")
    p_sym_top.add_argument(
        "--per-exchange",
        type=int,
        default=120,
        dest="per_exchange",
        help="每家交易所先取前 M 个再合并",
    )
    p_sym_top.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并行拉取交易所数量",
    )

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "premium":
        return _emit(
            calc_premium(
                amount=args.amount,
                symbol=args.symbol,
                days=args.days,
                leverage=args.leverage,
                credit_score=args.credit_score,
                solvency_ratio=args.solvency_ratio,
                product_tier=args.product_tier,
                large_policy=args.large_policy,
                max_premium_ratio=args.max_premium_ratio,
                current_atr=args.current_atr,
                base_atr=args.base_atr,
                spike_count_1h=args.spike_count_1h,
                use_report_risk=not getattr(args, "no_report_risk", False),
                report_days=getattr(args, "report_days", 30),
                monthly_approved_payouts=getattr(args, "monthly_approved_payouts", None),
            ),
            args,
        )

    if args.command == "reports":
        from .report_risk import build_report_risk_profile
        from .rate_table import rebuild_rate_table

        if args.rep_command == "refresh":
            return _emit(rebuild_rate_table(lookback_days=args.days), args)

        profile = build_report_risk_profile(args.symbol, args.days)
        if args.rep_command == "profile" and getattr(args, "with_premium", False):
            prem = calc_premium(
                args.amount,
                args.symbol,
                args.policy_days,
                args.leverage,
                solvency_ratio=args.solvency_ratio,
                use_report_risk=True,
                report_days=args.days,
            )
            return _emit({"success": True, "profile": profile, "premium": prem}, args)
        return _emit(profile, args)

    if args.command == "payout":
        return _emit(
            calc_payout(
                coverage=args.coverage,
                symbol=args.symbol,
                actual_amplitude=args.actual_amplitude,
                global_payout_today=args.global_payout_today,
                solvency_ratio=args.solvency_ratio,
                solvency_payout_ratio=args.solvency_payout_ratio,
            ),
            args,
        )

    if args.command == "haircut":
        return _emit(apply_haircut(args.pending, args.pool), args)

    if args.command == "solvency":
        if args.sol_command == "check":
            return _emit(
                SolvencyRiskManager.check_risk(args.pool, args.coverage),
                args,
            )
        if args.scenario == "all":
            return _emit(
                SolvencyRiskManager.run_all_scenarios(
                    args.pool, args.coverage, runs=args.runs, seed=args.seed
                ),
                args,
            )
        return _emit(
            SolvencyRiskManager.stress_test(
                args.pool,
                args.coverage,
                args.scenario,
                runs=args.runs,
                seed=args.seed,
            ),
            args,
        )

    if args.command == "backtest":
        if args.json:
            return _emit(_run_backtest_json(args), args)
        print_backtest_report(
            run_full_backtest(
                pool_size=args.pool,
                coverage_size=args.coverage,
                symbol=args.symbol,
                amount=args.amount,
                days=args.days,
                leverage=args.leverage,
                amplitude=args.amplitude,
                global_payout_today=args.global_payout_today,
            )
        )
        return 0

    if args.command == "live":
        return _dispatch_live(args)

    if args.command == "monitor":
        from .monitor import run_monitor_cycle

        return _emit(run_monitor_cycle(dry_run=args.dry_run), args)

    if args.command == "reset":
        from .monitor import reset_daily_quota

        return _emit(reset_daily_quota(source="cli"), args)

    if args.command == "symbols" and args.sym_command == "refresh":
        from .hot_symbols import fetch_top_hot_usdt_symbols, save_monitor_symbols

        result = fetch_top_hot_usdt_symbols(
            args.limit,
            per_exchange=args.per_exchange,
            workers=args.workers,
        )
        if result.get("success"):
            path = save_monitor_symbols(result)
            result["saved_to"] = str(path)
            result["hint"] = (
                "已写入 monitor_symbols.json；清空 .env 里 WICKSHIELD_MONITOR_SYMBOLS "
                "或删除该行后 restart，即自动用此列表"
            )
        return _emit(result, args)

    return 1


def _resolve_solvency_ratio(args: argparse.Namespace) -> float:
    if args.solvency_ratio is not None:
        return float(args.solvency_ratio)
    if args.pool is not None and args.coverage is not None:
        check = SolvencyRiskManager.check_risk(args.pool, args.coverage)
        if check.get("success"):
            return float(check["data"]["ratio"])
    return 50.0


def _dispatch_live(args: argparse.Namespace) -> int:
    from .market_data import build_market_snapshot

    try:
        market = build_market_snapshot(
            symbol=args.symbol,
            exchange=args.exchange,
            timeframe=args.timeframe,
            days_back=args.days_back,
        )
    except Exception as e:
        return _emit({"success": False, "error": f"行情获取失败: {e}"}, args)

    out: Dict[str, Any] = {"success": True, "market": market}

    if args.mode == "quote":
        return _emit(out, args)

    solvency_ratio = _resolve_solvency_ratio(args)
    current_atr = market.get("current_atr_percent")
    base_atr = market.get("base_atr_percent")

    premium = calc_premium(
        amount=args.amount,
        symbol=args.symbol,
        days=args.days,
        leverage=args.leverage,
        credit_score=args.credit_score,
        solvency_ratio=solvency_ratio,
        product_tier=args.product_tier,
        current_atr=current_atr,
        base_atr=base_atr,
        spike_count_1h=market.get("spike_count_1h", 0),
        use_report_risk=not getattr(args, "no_report_risk", False),
        report_days=getattr(args, "report_days", 30),
    )
    out["solvency_ratio_used"] = solvency_ratio
    out["premium"] = premium

    if args.mode == "premium":
        return _emit(out, args)

    amp = market.get("max_amplitude_1h_percent") or 0.0
    payout = calc_payout(
        coverage=args.amount,
        symbol=args.symbol,
        actual_amplitude=amp,
        global_payout_today=args.global_payout_today,
        solvency_ratio=solvency_ratio,
    )
    out["payout"] = payout
    if args.pool is not None and args.coverage is not None:
        out["solvency"] = SolvencyRiskManager.check_risk(args.pool, args.coverage)

    return _emit(out, args)


def _run_backtest_json(args: argparse.Namespace) -> Dict[str, Any]:
    solvency = SolvencyRiskManager.check_risk(args.pool, args.coverage)
    if not solvency["success"]:
        return solvency
    ratio = solvency["data"]["ratio"]
    premium = calc_premium(
        args.amount,
        args.symbol,
        args.days,
        args.leverage,
        solvency_ratio=ratio,
    )
    payout = calc_payout(
        args.amount,
        args.symbol,
        args.amplitude,
        global_payout_today=args.global_payout_today,
        solvency_ratio=ratio,
    )
    stress = SolvencyRiskManager.stress_test(args.pool, args.coverage, "severe", runs=50, seed=42)
    net = 0.0
    if premium.get("success") and payout.get("success"):
        net = premium["data"]["total_premium"] - payout["data"]["final_payout"]
    return {
        "success": True,
        "data": {
            "solvency": solvency["data"],
            "premium": premium.get("data"),
            "payout": payout.get("data"),
            "stress": stress.get("data"),
            "net_flow": net,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
