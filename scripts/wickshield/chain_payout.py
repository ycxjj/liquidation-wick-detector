"""
WickShield 链上 USDT：保费入账验证 + 理赔出款。
需配置 RPC、资金库私钥、USDT 合约地址。
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Dict, Optional

# ERC20 transfer(address,uint256) — 0xa9059cbb
_TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
_USDT_DECIMALS_DEFAULT = 6


def chain_payout_enabled() -> bool:
    return os.environ.get("WICKSHIELD_CHAIN_PAYOUT", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def rpc_url() -> str:
    return os.environ.get("WICKSHIELD_RPC_URL", "").strip()


def usdt_contract() -> str:
    return os.environ.get(
        "WICKSHIELD_USDT_CONTRACT",
        "0xFd086bC7CD5C481DCC9C702cE68A73c03b4dF123",
    ).strip()


def chain_id() -> int:
    return int(os.environ.get("WICKSHIELD_CHAIN_ID", "42161"))


def usdt_decimals() -> int:
    return int(os.environ.get("WICKSHIELD_USDT_DECIMALS", str(_USDT_DECIMALS_DEFAULT)))


def treasury_private_key() -> str:
    return os.environ.get("WICKSHIELD_TREASURY_PRIVATE_KEY", "").strip()


def treasury_address() -> str:
    explicit = os.environ.get("WICKSHIELD_TREASURY_ADDRESS", "").strip()
    if explicit:
        return explicit
    key = treasury_private_key()
    if not key:
        return ""
    try:
        from eth_account import Account

        return Account.from_key(key).address
    except Exception:
        return ""


def _to_base_units(amount_usdt: float) -> int:
    d = Decimal(str(amount_usdt))
    scale = Decimal(10) ** usdt_decimals()
    return int((d * scale).to_integral_value())


def _from_base_units(value: int) -> float:
    return float(Decimal(value) / (Decimal(10) ** usdt_decimals()))


def _get_web3():
    try:
        from web3 import Web3
    except ImportError as e:
        raise RuntimeError("需要安装 web3: pip install web3") from e
    url = rpc_url()
    if not url:
        raise RuntimeError("未配置 WICKSHIELD_RPC_URL")
    w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError("无法连接 RPC")
    return w3


def _usdt_contract(w3):
    abi = [
        {
            "constant": False,
            "inputs": [
                {"name": "_to", "type": "address"},
                {"name": "_value", "type": "uint256"},
            ],
            "name": "transfer",
            "outputs": [{"name": "", "type": "bool"}],
            "type": "function",
        },
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function",
        },
    ]
    return w3.eth.contract(
        address=w3.to_checksum_address(usdt_contract()), abi=abi
    )


def verify_premium_payment(
    tx_hash: str,
    *,
    from_wallet: str,
    expected_amount_usdt: float,
    min_confirmations: Optional[int] = None,
) -> Dict[str, Any]:
    """验证用户向资金库转入 USDT（ERC20 Transfer）。"""
    treasury = treasury_address()
    if not treasury:
        return {"success": False, "error": "未配置资金库地址"}
    try:
        w3 = _get_web3()
        tx_hash = tx_hash.strip()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        if receipt is None:
            return {"success": False, "error": "交易不存在或未上链"}
        status = receipt.get("status")
        if status != 1:
            return {"success": False, "error": "链上交易失败"}

        need_conf = min_confirmations
        if need_conf is None:
            need_conf = int(os.environ.get("WICKSHIELD_MIN_CONFIRMATIONS", "1"))
        if need_conf > 0:
            latest = w3.eth.block_number
            conf = latest - int(receipt["blockNumber"]) + 1
            if conf < need_conf:
                return {
                    "success": False,
                    "error": f"确认数不足 ({conf}/{need_conf})",
                    "confirmations": conf,
                }

        contract_addr = w3.to_checksum_address(usdt_contract()).lower()
        treasury_l = w3.to_checksum_address(treasury).lower()
        from_l = w3.to_checksum_address(from_wallet).lower()
        min_units = _to_base_units(expected_amount_usdt * 0.999)  # 允许极小误差

        from web3 import Web3

        transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex().lower()
        if not transfer_topic.startswith("0x"):
            transfer_topic = "0x" + transfer_topic

        matched = False
        amount_units = 0
        for log in receipt["logs"]:
            if log["address"].lower() != contract_addr:
                continue
            topics = [t.hex() if hasattr(t, "hex") else str(t) for t in log["topics"]]
            if len(topics) < 3:
                continue
            t0 = topics[0].lower()
            if not t0.startswith("0x"):
                t0 = "0x" + t0
            if t0 != transfer_topic.lower():
                continue
            t_from = Web3.to_checksum_address("0x" + topics[1][-40:])
            t_to = Web3.to_checksum_address("0x" + topics[2][-40:])
            t_from = t_from.lower()
            t_to = t_to.lower()
            if t_from != from_l or t_to != treasury_l:
                continue
            amount_units = int(log["data"].hex(), 16)
            if amount_units >= min_units:
                matched = True
                break

        if not matched:
            return {
                "success": False,
                "error": "未找到符合条件的 USDT 转入资金库记录",
            }
        return {
            "success": True,
            "tx_hash": tx_hash,
            "amount_usdt": _from_base_units(amount_units),
            "from": from_wallet,
            "to": treasury,
            "block_number": int(receipt["blockNumber"]),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_usdt_payout(
    to_address: str,
    amount_usdt: float,
    *,
    reference: Optional[str] = None,
) -> Dict[str, Any]:
    """从资金库向用户赔付地址发送 USDT。"""
    if not chain_payout_enabled():
        return {"success": False, "error": "链上赔付未开启", "skipped": True}
    key = treasury_private_key()
    if not key:
        return {"success": False, "error": "未配置 WICKSHIELD_TREASURY_PRIVATE_KEY"}
    try:
        w3 = _get_web3()
        from eth_account import Account

        acct = Account.from_key(key)
        token = _usdt_contract(w3)
        to_checksum = w3.to_checksum_address(to_address)
        value = _to_base_units(amount_usdt)
        if value <= 0:
            return {"success": False, "error": "赔付金额须 > 0"}

        nonce = w3.eth.get_transaction_count(acct.address)
        gas_price = w3.eth.gas_price
        tx = token.functions.transfer(to_checksum, value).build_transaction(
            {
                "chainId": chain_id(),
                "from": acct.address,
                "nonce": nonce,
                "gasPrice": gas_price,
            }
        )
        try:
            tx["gas"] = w3.eth.estimate_gas(tx)
        except Exception:
            tx["gas"] = int(os.environ.get("WICKSHIELD_PAYOUT_GAS", "120000"))

        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(
            signed, "rawTransaction", None
        )
        if raw is None:
            return {"success": False, "error": "签名失败"}
        tx_hash = w3.eth.send_raw_transaction(raw)
        h = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        return {
            "success": True,
            "tx_hash": h,
            "to": to_address,
            "amount_usdt": amount_usdt,
            "reference": reference,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def dispatch_claim_payout(
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """
    approved 理赔：优先链上 USDT；失败或未配置时回退 Webhook。
    """
    to_addr = (
        entry.get("payout_address")
        or entry.get("wallet_address")
        or ""
    ).strip()
    amount = float(entry.get("approved_payout") or entry.get("final_payout") or 0)
    if not to_addr or amount <= 0:
        return {"success": False, "error": "缺少赔付地址或金额"}

    if chain_payout_enabled():
        ref = f"policy:{entry.get('policy_id')}|{entry.get('symbol')}"
        result = send_usdt_payout(to_addr, amount, reference=ref)
        entry["chain_payout"] = result
        if result.get("success"):
            entry["payout_tx_hash"] = result.get("tx_hash")
            return result

    import json
    import urllib.request

    url = os.environ.get("WICKSHIELD_CHAIN_WEBHOOK", "").strip()
    if url:
        try:
            body = json.dumps(entry, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                entry["chain_dispatch_status"] = resp.status
        except Exception as e:
            entry["chain_dispatch_error"] = str(e)
    return {
        "success": bool(entry.get("chain_dispatch_status")),
        "webhook": True,
        "chain_dispatch_status": entry.get("chain_dispatch_status"),
        "chain_dispatch_error": entry.get("chain_dispatch_error"),
    }
